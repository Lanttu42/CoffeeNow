"""Web Push -lähetys. Yksityinen VAPID-avain luetaan VAIN ympäristömuuttujasta."""
import json
import logging
import os
import time
from urllib.parse import urlsplit

from pywebpush import WebPushException, webpush

from . import db

log = logging.getLogger("coffeenow.push")

VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com")

# Palvelin lähettää ilmoitukset tilauksen endpoint-osoitteeseen. Jotta sitä ei voi käyttää lähettämään
# pyyntöjä mihin tahansa (SSRF), hyväksytään vain selainten omat push-palvelut.
# PUSH_ALLOWED_HOSTS (pilkuilla eroteltu) korvaa listan, esim. testeissä.
PUSH_ALLOWED_HOSTS = [h.strip().lower() for h in os.environ.get(
    "PUSH_ALLOWED_HOSTS",
    "fcm.googleapis.com,android.googleapis.com,.push.services.mozilla.com,.push.apple.com,.notify.windows.com",
).split(",") if h.strip()]


def endpoint_allowed(endpoint: str) -> bool:
    try:
        u = urlsplit(endpoint)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not host or u.port not in (None, 443) or u.username or u.password:
        return False
    return any(host.endswith(h) if h.startswith(".") else host == h for h in PUSH_ALLOWED_HOSTS)


def enabled() -> bool:
    return bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)


def notify_machine(machine_id: str, title: str, body, skip_device: str | None = None) -> None:
    """Lähettää ilmoituksen kaikille keittimen seuraajille (paitsi lähettäjälle itselleen).
    body on joko teksti tai funktio kieli -> teksti, jolloin jokainen saa ilmoituksen omalla kielellään."""
    if not enabled():
        log.info("Push ei käytössä (VAPID-avaimet puuttuvat): %s / %s", title, body)
        return

    def payload(lang: str) -> str:
        text = body(lang or "fi") if callable(body) else body
        return json.dumps({"title": title, "body": text, "url": f"/m/{machine_id}"})

    with db.connect() as con:
        # Yksityisellä keittimellä vain nykyiset jäsenet (varmistus siivouksen lisäksi)
        subs = con.execute(
            "SELECT s.endpoint, s.device_id, s.p256dh, s.auth, s.lang FROM subscriptions s"
            " JOIN machines m ON m.id = s.machine_id"
            " WHERE s.machine_id = ? AND (m.org_id IS NULL OR EXISTS ("
            "   SELECT 1 FROM memberships ms WHERE ms.org_id = m.org_id AND ms.user_id = s.user_id))"
            " AND NOT EXISTS (SELECT 1 FROM push_mutes pm WHERE pm.endpoint = s.endpoint AND pm.until > ?)",
            (machine_id, time.time()),
        ).fetchall()

    dead = []
    for s in subs:
        if skip_device and s["device_id"] == skip_device:
            continue
        try:
            webpush(
                subscription_info={"endpoint": s["endpoint"], "keys": {"p256dh": s["p256dh"], "auth": s["auth"]}},
                data=payload(s["lang"]),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_SUBJECT},
                ttl=3600,
            )
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):  # tilaus vanhentunut -> siivotaan pois
                dead.append(s["endpoint"])
            else:
                log.warning("Push epäonnistui (%s)", status)
        except ValueError:  # rikkinäiset avaimet tilauksessa -> ei koskaan toimi, siivotaan
            dead.append(s["endpoint"])
        except Exception as e:  # verkkovirhe tms. ei saa kaataa muiden ilmoituksia
            log.warning("Push epäonnistui: %s", type(e).__name__)

    if dead:
        with db.connect() as con:
            con.executemany("DELETE FROM subscriptions WHERE endpoint = ?", [(d,) for d in dead])
        log.info("Poistettiin %d vanhentunutta tilausta", len(dead))
