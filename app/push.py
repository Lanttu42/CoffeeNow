"""Web Push -lähetys. Yksityinen VAPID-avain luetaan VAIN ympäristömuuttujasta."""
import json
import logging
import os

from pywebpush import WebPushException, webpush

from . import db

log = logging.getLogger("coffeenow.push")

VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com")


def enabled() -> bool:
    return bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)


def notify_machine(machine_id: str, title: str, body: str, skip_device: str | None = None) -> None:
    """Lähettää ilmoituksen kaikille keittimen seuraajille (paitsi lähettäjälle itselleen)."""
    if not enabled():
        log.info("Push ei käytössä (VAPID-avaimet puuttuvat): %s / %s", title, body)
        return

    payload = json.dumps({"title": title, "body": body, "url": f"/m/{machine_id}"})

    with db.connect() as con:
        subs = con.execute(
            "SELECT endpoint, device_id, p256dh, auth FROM subscriptions WHERE machine_id = ?",
            (machine_id,),
        ).fetchall()

    dead = []
    for s in subs:
        if skip_device and s["device_id"] == skip_device:
            continue
        try:
            webpush(
                subscription_info={"endpoint": s["endpoint"], "keys": {"p256dh": s["p256dh"], "auth": s["auth"]}},
                data=payload,
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
