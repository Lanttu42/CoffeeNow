"""API- ja oikeustestit. Käynnistää oman palvelimen väliaikaisella kannalla.

Ajo: python3 tests/test_api.py
"""
import base64
import http.cookiejar
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

ROOT = Path(__file__).resolve().parent.parent
PORT = 8766
BASE = f"http://127.0.0.1:{PORT}"
PASSED = 0


class Client:
    """Oma evästepurkki = oma käyttäjä/laite."""

    def __init__(self, device: str):
        self.device = device
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", "Accept-Language": "fi", **(headers or {})})
        try:
            with self.opener.open(req) as r:
                raw = r.read()
                ctype = r.headers.get_content_type()
                return r.status, json.loads(raw) if raw and ctype == "application/json" else raw
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw


def check(name, cond, info=None):
    global PASSED
    if not cond:
        print(f"FAIL {name}  {info if info is not None else ''}")
        raise SystemExit(1)
    PASSED += 1
    print(f"OK   {name}")


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def fake_subscription(tag: str) -> dict:
    pk = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"endpoint": f"https://push.invalid/{tag}", "keys": {"p256dh": b64(pk), "auth": b64(os.urandom(16))}}


def wait_rate_limit():
    time.sleep(5.1)


def run():
    anon, alice, bob, eve = Client("anon-device-1"), Client("alice-device"), Client("bob-device-1"), Client("eve-device-1")
    pw = "kahvi-on-hyvaa-123"

    # ---------- julkinen keitin (v1-toiminnallisuus) ----------
    s, cfg = anon.call("GET", "/api/config")
    check("config", s == 200 and cfg["push_enabled"])
    check("config: versio ja build", cfg["version"] and len(cfg["build"]) == 10, cfg)

    # ---------- kielet ----------
    bad = "/api/machines/00000000-0000-0000-0000-000000000000"
    check("virhe englanniksi, jos kieltä ei tunnisteta",
          anon.call("GET", bad, headers={"Accept-Language": "de-DE,de;q=0.9"})[1]["detail"] == "Coffee maker not found")
    check("virhe ruotsiksi Accept-Languagesta",
          anon.call("GET", bad, headers={"Accept-Language": "sv-FI,sv;q=0.9,fi;q=0.8"})[1]["detail"] == "Kaffebryggaren hittades inte")
    check("q-arvot huomioidaan",
          anon.call("GET", bad, headers={"Accept-Language": "en;q=0.5,fi;q=0.9"})[1]["detail"] == "Kahvinkeitintä ei löydy")
    check("X-Lang voittaa selaimen kielen",
          anon.call("GET", bad, headers={"Accept-Language": "fi", "X-Lang": "en"})[1]["detail"] == "Coffee maker not found")
    check("koodit (login_required) ennallaan kielestä riippumatta",
          anon.call("POST", "/api/orgs", {"name": "x"}, headers={"X-Lang": "sv"})[1]["detail"] == "login_required")
    s, raw = anon.call("GET", "/")
    html = raw.decode() if isinstance(raw, bytes) else str(raw)
    check("index.html: build tiedostojen osoitteissa", f"app.js?v={cfg['build']}" in html and "{{" not in html)
    s, pub = anon.call("POST", "/api/machines", {"name": "Julkinen keitin", "device_id": anon.device})
    check("anonyymi luo julkisen keittimen", s == 201 and pub["org"] is None)
    check("anonyymi lukee julkisen", anon.call("GET", f"/api/machines/{pub['id']}")[0] == 200)
    check("tyhjä nimi 422", anon.call("POST", "/api/machines", {"name": ""})[0] == 422)
    # ---------- täyden pannun asetus ----------
    s, m = anon.call("GET", f"/api/machines/{pub['id']}", headers={"X-Device-Id": anon.device})
    check("täysi pannu oletus 10, luoja saa muokata", m["full_pot_cups"] == 10 and m["can_edit"] is True, m)
    check("muu laite ei saa muokata", eve.call("GET", f"/api/machines/{pub['id']}", headers={"X-Device-Id": eve.device})[1]["can_edit"] is False)
    check("muu laite: asetus 403", eve.call("POST", f"/api/machines/{pub['id']}/settings", {"full_pot_cups": 144},
                                            headers={"X-Device-Id": eve.device})[0] == 403)
    check("liian iso pannu 422", anon.call("POST", f"/api/machines/{pub['id']}/settings", {"full_pot_cups": 1000},
                                          headers={"X-Device-Id": anon.device})[0] == 422)
    s, m = anon.call("POST", f"/api/machines/{pub['id']}/settings", {"full_pot_cups": 144}, headers={"X-Device-Id": anon.device})
    check("luoja asettaa 144 kuppia", s == 200 and m["full_pot_cups"] == 144, m)
    check("144 kuppia kelpaa tilaan", anon.call("POST", f"/api/machines/{pub['id']}/status",
          {"status": "brewing", "cups": 144, "device_id": anon.device})[1]["cups"] == 144)
    wait_rate_limit()
    check("pelkät välilyönnit 422", anon.call("POST", "/api/machines", {"name": "   "})[0] == 422)
    s, m = anon.call("POST", "/api/machines", {"name": "Keitin\u202e\x00\n  2"})
    check("ohjausmerkit siivotaan nimestä", s == 201 and m["name"] == "Keitin 2", m)

    # ---------- kahvityyppi, "ehkä", keittoaika, automaattinen valmistuminen ----------
    dev = {"X-Device-Id": anon.device}
    s, bm = anon.call("POST", "/api/machines", {"name": "Pannutesti", "device_id": anon.device})
    bid = bm["id"]
    check("oletustyypit ja keittoaika 1 min/kuppi",
          bm["coffee_types"] == ["Tavallinen", "Tumma paahto", "Kofeiiniton"] and bm["brew_min_per_cup"] == 1.0, bm)
    check("keittoaika > 30 min/kuppi 422",
          anon.call("POST", f"/api/machines/{bid}/settings", {"brew_min_per_cup": 31}, headers=dev)[0] == 422)
    check("tyhjä tyyppilista 422",
          anon.call("POST", f"/api/machines/{bid}/settings", {"coffee_types": []}, headers=dev)[0] == 422)
    s, bm = anon.call("POST", f"/api/machines/{bid}/settings",
                      {"coffee_types": ["Tumma paahto", "  Vaalea\n ", "vaalea"]}, headers=dev)
    check("tyypit siivotaan ja tuplat poistetaan", s == 200 and bm["coffee_types"] == ["Tumma paahto", "Vaalea"], bm)
    check("muut asetukset säilyvät osittaisessa päivityksessä", bm["full_pot_cups"] == 10 and bm["brew_min_per_cup"] == 1.0, bm)
    check("eve ei saa muuttaa tyyppejä",
          eve.call("POST", f"/api/machines/{bid}/settings", {"coffee_types": ["x"]}, headers={"X-Device-Id": eve.device})[0] == 403)
    check("tuntematon tyyppi 400", anon.call("POST", f"/api/machines/{bid}/status",
          {"status": "brewing", "cups": 4, "coffee_type": "Olut", "device_id": anon.device})[0] == 400)
    wait_rate_limit()
    t0 = time.time()
    s, bm = anon.call("POST", f"/api/machines/{bid}/status",
                      {"status": "brewing", "cups": 4, "coffee_type": "Vaalea", "device_id": anon.device})
    check("valikosta: tyyppi + kupit, ei 'ehkä'",
          s == 200 and bm["status"] == "brewing" and bm["cups"] == 4 and bm["brew_type"] == "Vaalea" and not bm["estimated"], bm)
    check("valmistumisaika = 4 kuppia x 1 min", abs(bm["brew_ready_at"] - (t0 + 240)) < 5, bm)
    wait_rate_limit()
    s, bm = anon.call("POST", f"/api/machines/{bid}/status", {"status": "brewing", "quick": True, "cups": 99, "device_id": anon.device})
    check("pikailmoitus: edelliset arvot + 'ehkä' (asiakkaan kupit ohitetaan)",
          bm["cups"] == 4 and bm["brew_type"] == "Vaalea" and bm["estimated"] is True, bm)
    # Siirretään valmistumisaika menneisyyteen -> taustasilmukka vaihtaa tilan
    import sqlite3
    dbc = sqlite3.connect(os.environ["COFFEENOW_DB"])
    dbc.execute("UPDATE machines SET brew_ready_at = ? WHERE id = ?", (time.time() - 1, bid))
    dbc.commit()
    for _ in range(30):
        bm = anon.call("GET", f"/api/machines/{bid}")[1]
        if bm["status"] == "ready":
            break
        time.sleep(0.2)
    check("keittoajan jälkeen tila vaihtuu itsestään: kahvia on",
          bm["status"] == "ready" and bm["brew_ready_at"] is None and bm["brew_type"] == "Vaalea" and bm["cups"] == 4, bm)
    wait_rate_limit()
    bm = anon.call("POST", f"/api/machines/{bid}/status", {"status": "empty", "device_id": anon.device})[1]
    check("kahvi loppu tyhjentää pannun tiedot", bm["cups"] is None and bm["brew_type"] is None and not bm["estimated"], bm)
    anon.call("POST", f"/api/machines/{bid}/settings", {"brew_min_per_cup": 0}, headers=dev)
    wait_rate_limit()
    bm = anon.call("POST", f"/api/machines/{bid}/status",
                   {"status": "brewing", "cups": 2, "coffee_type": "Tumma paahto", "device_id": anon.device})[1]
    check("keittoaika 0: kahvi on heti valmista", bm["status"] == "ready" and bm["brew_type"] == "Tumma paahto"
          and bm["cups"] == 2 and bm["brew_ready_at"] is None, bm)
    s, bm = anon.call("POST", f"/api/machines/{bid}/settings", {"brew_min_per_cup": 1.26}, headers=dev)
    check("keittoaika pyöristetään puoleen minuuttiin", bm["brew_min_per_cup"] == 1.5, bm)
    s, bm = anon.call("POST", f"/api/machines/{pub['id']}/settings", {"full_pot_cups": 144}, headers=dev)
    check("vanha asiakas (vain full_pot_cups) toimii yhä", s == 200 and bm["coffee_types"][0] == "Tavallinen", bm)
    sub = fake_subscription("ssrf")
    for bad in ("https://example.com/x", "https://127.0.0.1/x", "https://push.invalid.evil.com/x", "https://push.invalid:8443/x"):
        sub["endpoint"] = bad
        check(f"tuntematon push-endpoint hylätään: {bad}",
              anon.call("POST", f"/api/machines/{pub['id']}/follow", {"device_id": anon.device, "subscription": sub})[0] == 422)
    sub["endpoint"] = "https://fcm.googleapis.com/fcm/send/abc"
    check("FCM-endpoint kelpaa", anon.call("POST", f"/api/machines/{pub['id']}/follow", {"device_id": anon.device, "subscription": sub})[0] == 204)

    # ---------- hiljennys ----------
    ep = sub["endpoint"]
    check("hiljennys vaatii tilauksen",
          anon.call("POST", "/api/push/mute", {"endpoint": "https://fcm.googleapis.com/fcm/send/eiole", "minutes": 60})[0] == 404)
    check("vain sallitut kestot", anon.call("POST", "/api/push/mute", {"endpoint": ep, "minutes": 5})[0] == 422)
    s2, r = anon.call("POST", "/api/push/mute", {"endpoint": ep, "minutes": 240})
    check("hiljennys 4 h", s2 == 200 and 4 * 3600 - 60 < r["until"] - time.time() <= 4 * 3600, r)
    check("hiljennys toistaiseksi", anon.call("POST", "/api/push/mute", {"endpoint": ep, "minutes": None}) == (200, {"until": None}))
    import sqlite3 as _sq
    _c = _sq.connect(os.environ["COFFEENOW_DB"])
    check("hiljennys tallessa", _c.execute("SELECT until > ? FROM push_mutes WHERE endpoint = ?", (time.time() + 10**8, ep)).fetchone() == (1,))
    check("hiljennyksen poisto", anon.call("POST", "/api/push/unmute", {"endpoint": ep})[0] == 204
          and _c.execute("SELECT COUNT(*) FROM push_mutes WHERE endpoint = ?", (ep,)).fetchone() == (0,))
    check("huono uuid 404", anon.call("GET", "/api/machines/xyz")[0] == 404)

    # ---------- rekisteröinti & kirjautuminen ----------
    s, r = alice.call("POST", "/api/auth/register", {"username": "alice", "password": pw, "password2": "eri"})
    check("salasanat eivät täsmää", s == 400)
    s, r = alice.call("POST", "/api/auth/register", {"username": "alice", "password": "lyhyt", "password2": "lyhyt"})
    check("liian lyhyt salasana", s == 400)
    s, r = alice.call("POST", "/api/auth/register", {"username": "a b<script>", "password": pw, "password2": pw})
    check("kelvoton tunnus", s == 400)
    s, r = alice.call("POST", "/api/auth/register", {"username": "alice", "password": pw, "password2": pw})
    check("alice rekisteröityy", s == 201 and r["username"] == "alice")
    s, r = bob.call("POST", "/api/auth/register", {"username": "ALICE", "password": pw, "password2": pw})
    check("tunnus varattu (kirjainkoko ei ratkaise)", s == 409)
    check("bob rekisteröityy", bob.call("POST", "/api/auth/register", {"username": "bob", "password": pw, "password2": pw})[0] == 201)
    check("eve rekisteröityy", eve.call("POST", "/api/auth/register", {"username": "eve", "password": pw, "password2": pw})[0] == 201)

    s, me = alice.call("GET", "/api/auth/me")
    check("me palauttaa käyttäjän", me["user"]["username"] == "alice")
    check("anonyymi me = null", anon.call("GET", "/api/auth/me")[1]["user"] is None)

    tmp = Client("tmp-device-1")
    check("väärä salasana 401", tmp.call("POST", "/api/auth/login", {"username": "alice", "password": "vaarin-meni"})[0] == 401)
    check("tuntematon käyttäjä 401 (sama viesti)",
          tmp.call("POST", "/api/auth/login", {"username": "nobody", "password": "vaarin-meni"})[1]["detail"] == "Väärä käyttäjätunnus tai salasana")
    check("oikea kirjautuminen", tmp.call("POST", "/api/auth/login", {"username": "alice", "password": pw})[0] == 200)
    check("uloskirjautuminen", tmp.call("POST", "/api/auth/logout")[0] == 204)
    check("sessio oikeasti kuollut", tmp.call("GET", "/api/auth/me")[1]["user"] is None)

    # ---------- organisaatio ----------
    check("anonyymi ei voi luoda organisaatiota", anon.call("POST", "/api/orgs", {"name": "X"})[0] == 401)
    s, org = alice.call("POST", "/api/orgs", {"name": "Kofeiinikopla"})
    check("alice luo organisaation", s == 201)
    oid = org["id"]
    s, o = alice.call("GET", f"/api/orgs/{oid}")
    code = o["invite_code"]
    check("kutsukoodi muotoa XXX-XXX", len(code) == 7 and code[3] == "-", code)
    check("perustaja on manageri", o["is_founder"] and o["is_manager"])

    check("ulkopuolinen ei näe organisaatiota (404)", eve.call("GET", f"/api/orgs/{oid}")[0] == 404)
    check("väärä kutsukoodi 404", eve.call("POST", "/api/orgs/join", {"code": "AAA-AAA"})[0] == 404)
    s, j = bob.call("POST", "/api/orgs/join", {"code": code.lower().replace("-", " ")})
    check("bob liittyy (koodi normalisoidaan)", s == 200 and j["id"] == oid)
    s, o = bob.call("GET", f"/api/orgs/{oid}")
    check("bob on jäsen, ei manageri", s == 200 and not o["is_manager"])

    # ---------- yksityinen keitin ----------
    check("bob (ei manageri) ei voi liittää keitintä",
          bob.call("POST", f"/api/orgs/{oid}/machines", {"machine_id": pub["id"]})[0] == 403)
    s, priv = alice.call("POST", "/api/machines", {"name": "Salainen keitin", "org_id": oid})
    check("manageri luo keittimen suoraan organisaatioon", s == 201 and priv["org"]["name"] == "Kofeiinikopla")
    check("bob ei voi luoda keitintä organisaatioon", bob.call("POST", "/api/machines", {"name": "x", "org_id": oid})[0] == 403)
    pid = priv["id"]

    s, r = anon.call("GET", f"/api/machines/{pid}")
    check("anonyymi -> 401 login_required", s == 401 and r["detail"] == "login_required")
    check("401 ei paljasta nimeä", "Salainen" not in json.dumps(r) and "Kofeiini" not in json.dumps(r))
    s, r = eve.call("GET", f"/api/machines/{pid}")
    check("ulkopuolinen -> 403 ilman nimeä", s == 403 and "Salainen" not in json.dumps(r))
    check("ulkopuolinen ei saa QR:ää", eve.call("GET", f"/api/machines/{pid}/qr.svg")[0] == 403)
    check("ulkopuolinen ei voi päivittää tilaa",
          eve.call("POST", f"/api/machines/{pid}/status", {"status": "ready", "device_id": eve.device})[0] == 403)
    check("ulkopuolinen ei voi toivoa", eve.call("POST", f"/api/machines/{pid}/wish", {"device_id": eve.device})[0] == 403)
    check("ulkopuolinen ei voi seurata",
          eve.call("POST", f"/api/machines/{pid}/follow", {"device_id": eve.device, "subscription": fake_subscription("eve")})[0] == 403)
    check("anonyymi ei voi seurata",
          anon.call("POST", f"/api/machines/{pid}/follow", {"device_id": anon.device, "subscription": fake_subscription("anon")})[0] == 401)

    me = bob.call("GET", "/api/auth/me")[1]
    check("jäsenen /me sisältää organisaation keittimet", any(x["id"] == pid for x in me["machines"]), me)
    check("ulkopuolisen /me ei sisällä niitä", not any(x["id"] == pid for x in eve.call("GET", "/api/auth/me")[1]["machines"]))
    # ---------- TOP 10 Brewers ----------
    s, top = bob.call("GET", f"/api/machines/{pid}/top")
    check("top-lista aluksi tyhjä", s == 200 and top["top"] == [] and top["me"] == "bob", top)
    check("ulkopuolinen ei näe top-listaa", eve.call("GET", f"/api/machines/{pid}/top")[0] == 403)
    alice.call("POST", f"/api/machines/{pid}/status", {"status": "brewing", "cups": 8, "device_id": alice.device})
    wait_rate_limit()
    alice.call("POST", f"/api/machines/{pid}/status", {"status": "ready", "device_id": alice.device})
    top = alice.call("GET", f"/api/machines/{pid}/top")[1]["top"]
    check("tulossa + valmis = yksi pannu", top == [{"username": "alice", "brews": 1}], top)
    wait_rate_limit()
    bob.call("POST", f"/api/machines/{pid}/status", {"status": "empty", "device_id": bob.device})
    top = alice.call("GET", f"/api/machines/{pid}/top")[1]["top"]
    check("loppu alle 5 min pannun jälkeen mitätöi pannun", top == [], top)
    wait_rate_limit()
    alice.call("POST", f"/api/machines/{pid}/status", {"status": "ready", "device_id": alice.device})
    wait_rate_limit()
    bob.call("POST", f"/api/machines/{pid}/status", {"status": "low", "device_id": bob.device})
    top = alice.call("GET", f"/api/machines/{pid}/top")[1]["top"]
    check("myös 'Kahvia on' + vähissä heti perään mitätöityy", top == [], top)
    wait_rate_limit()
    alice.call("POST", f"/api/machines/{pid}/status", {"status": "ready", "device_id": alice.device})
    wait_rate_limit()
    alice.call("POST", f"/api/machines/{pid}/status", {"status": "supplies_low", "device_id": alice.device})
    wait_rate_limit()
    alice.call("POST", f"/api/machines/{pid}/status", {"status": "ready", "device_id": alice.device})
    top = alice.call("GET", f"/api/machines/{pid}/top")[1]["top"]
    check("16 min jäähy: ei toista pannua heti", top == [{"username": "alice", "brews": 1}], top)
    wait_rate_limit()

    check("jäsen ei saa muuttaa asetuksia", bob.call("POST", f"/api/machines/{pid}/settings", {"full_pot_cups": 20})[0] == 403)
    s2, m2 = alice.call("POST", f"/api/machines/{pid}/settings", {"full_pot_cups": 20})
    check("ylläpitäjä muuttaa asetuksia", s2 == 200 and m2["full_pot_cups"] == 20 and m2["can_edit"], m2)
    s, m = bob.call("GET", f"/api/machines/{pid}")
    check("jäsen näkee keittimen", s == 200 and m["name"] == "Salainen keitin" and m["org"]["is_manager"] is False)
    s, m = bob.call("POST", f"/api/machines/{pid}/status", {"status": "brewing", "cups": 6, "device_id": bob.device})
    check("jäsen päivittää tilan", s == 200 and m["cups"] == 6)
    check("jäsen seuraa", bob.call("POST", f"/api/machines/{pid}/follow", {"device_id": bob.device, "subscription": fake_subscription("bob")})[0] == 204)

    # ---------- julkisen keittimen liittäminen siivoaa ulkopuoliset tilaukset ----------
    check("anonyymi seuraa julkista",
          anon.call("POST", f"/api/machines/{pub['id']}/follow", {"device_id": anon.device, "subscription": fake_subscription("anon-pub")})[0] == 204)
    check("anonyymi toivoo julkiselta", anon.call("POST", f"/api/machines/{pub['id']}/wish", {"device_id": anon.device})[0] == 200)
    check("ylläpitäjä ei voi kaapata toisen luomaa julkista keitintä",
          alice.call("POST", f"/api/orgs/{oid}/machines", {"machine_id": pub["id"], "device_id": alice.device})[0] == 403)
    # Anonyymi luoja voi myöhemmin kirjautua ja liittää oman keittimensä samalta laitteelta
    s, own = alice.call("POST", "/api/machines", {"name": "Alicen julkinen", "device_id": "alice-old-phone"})
    check("ylläpitäjä liittää itse luomansa keittimen (laite-ID)",
          alice.call("POST", f"/api/orgs/{oid}/machines", {"machine_id": own["id"], "device_id": "alice-old-phone"})[0] == 204)
    check("luojan laite-ID ei näy API:ssa", "alice-old-phone" not in json.dumps(alice.call("GET", f"/api/machines/{own['id']}")[1]))
    # Siirretään pub alicelle testin loppuosaa varten (luoja = anon-laite)
    check("luojan laitteella liitos onnistuu", alice.call("POST", f"/api/orgs/{oid}/machines", {"machine_id": pub["id"], "device_id": anon.device})[0] == 204)
    check("liitetty keitin on nyt yksityinen", anon.call("GET", f"/api/machines/{pub['id']}")[0] == 401)
    s, m = alice.call("GET", f"/api/machines/{pub['id']}")
    check("liitetyn keittimen toiveet nollattu", m["wishes"] == 0)

    import sqlite3
    con = sqlite3.connect(os.environ["COFFEENOW_DB"])
    n = con.execute("SELECT COUNT(*) FROM subscriptions WHERE machine_id = ? AND user_id IS NULL", (pub["id"],)).fetchone()[0]
    check("ulkopuolisten push-tilaukset siivottu", n == 0)

    s, org2 = eve.call("POST", "/api/orgs", {"name": "Evenkerho"})
    check("eve ei voi kaapata toisen organisaation keitintä",
          eve.call("POST", f"/api/orgs/{org2['id']}/machines", {"machine_id": pid})[0] == 409)

    # ---------- roolit ----------
    bob_id = next(x["id"] for x in alice.call("GET", f"/api/orgs/{oid}")[1]["members"] if x["username"] == "bob")
    alice_id = alice.call("GET", "/api/auth/me")[1]["user"]["id"]
    check("bob ei voi nimetä managereita", bob.call("POST", f"/api/orgs/{oid}/members/{bob_id}/manager", {"is_manager": True})[0] == 403)
    check("bob ei voi poistaa organisaatiota", bob.call("POST", f"/api/orgs/{oid}/delete", {"confirm_name": "Kofeiinikopla"})[0] == 403)
    check("bob ei voi muuttaa asetuksia", bob.call("POST", f"/api/orgs/{oid}/settings", {"auto_manager": True})[0] == 403)
    check("perustaja nimeää bobin manageriksi", alice.call("POST", f"/api/orgs/{oid}/members/{bob_id}/manager", {"is_manager": True})[0] == 204)
    check("bob (manageri) voi vaihtaa kutsukoodin", bob.call("POST", f"/api/orgs/{oid}/invite/rotate")[0] == 200)
    check("vanha kutsukoodi ei enää toimi", eve.call("POST", "/api/orgs/join", {"code": code})[0] == 404)
    check("bob (manageri, ei perustaja) ei silti voi poistaa organisaatiota",
          bob.call("POST", f"/api/orgs/{oid}/delete", {"confirm_name": "Kofeiinikopla"})[0] == 403)
    check("perustajaa ei voi alentaa", alice.call("POST", f"/api/orgs/{oid}/members/{alice_id}/manager", {"is_manager": False})[0] == 400)
    check("perustaja ei voi erota", alice.call("POST", f"/api/orgs/{oid}/leave")[0] == 400)

    # auto_manager
    check("perustaja laittaa auto-managerit päälle", alice.call("POST", f"/api/orgs/{oid}/settings", {"auto_manager": True})[0] == 204)
    new_code = alice.call("GET", f"/api/orgs/{oid}")[1]["invite_code"]
    carol = Client("carol-device")
    carol.call("POST", "/api/auth/register", {"username": "carol", "password": pw, "password2": pw})
    carol.call("POST", "/api/orgs/join", {"code": new_code})
    check("auto-manager: carol on heti manageri", carol.call("GET", f"/api/orgs/{oid}")[1]["is_manager"] is True)

    # jäsenen poisto siivoaa tilaukset
    carol.call("POST", f"/api/machines/{pid}/follow", {"device_id": carol.device, "subscription": fake_subscription("carol")})
    carol_id = carol.call("GET", "/api/auth/me")[1]["user"]["id"]
    check("perustaja poistaa carolin", alice.call("POST", f"/api/orgs/{oid}/members/{carol_id}/remove")[0] == 204)
    check("poistettu ei enää näe keitintä", carol.call("GET", f"/api/machines/{pid}")[0] == 403)
    n = con.execute("SELECT COUNT(*) FROM subscriptions WHERE user_id = ?", (carol_id,)).fetchone()[0]
    check("poistetun jäsenen tilaukset siivottu", n == 0)

    # ---------- tilin poisto ----------
    check("perustaja ei voi poistaa tiliään", alice.call("POST", "/api/auth/delete", {"password": pw})[0] == 409)
    check("väärä salasana tilin poistossa", carol.call("POST", "/api/auth/delete", {"password": "vaarin-meni"})[0] == 401)
    check("carol poistaa tilinsä", carol.call("POST", "/api/auth/delete", {"password": pw})[0] == 204)
    check("poistettu tili ei kirjaudu", carol.call("POST", "/api/auth/login", {"username": "carol", "password": pw})[0] == 401)

    # ---------- salasanan vaihto ----------
    s, _ = bob.call("POST", "/api/auth/password", {"old_password": pw, "new_password": "uusi-salasana-456", "new_password2": "uusi-salasana-456"})
    check("bob vaihtaa salasanan", s == 204)
    check("uusi salasana toimii", Client("x-device-1").call("POST", "/api/auth/login", {"username": "bob", "password": "uusi-salasana-456"})[0] == 200)

    # ---------- organisaation poisto ----------
    check("väärä vahvistusnimi", alice.call("POST", f"/api/orgs/{oid}/delete", {"confirm_name": "kofeiinikopla"})[0] == 400)
    check("perustaja poistaa organisaation", alice.call("POST", f"/api/orgs/{oid}/delete", {"confirm_name": "Kofeiinikopla"})[0] == 204)
    check("organisaation keittimet poistuivat", bob.call("GET", f"/api/machines/{pid}")[0] == 404)
    check("bobin org-lista tyhjä", bob.call("GET", "/api/auth/me")[1]["orgs"] == [])

    # ---------- brute force -suojat ----------
    brute = Client("brute-device")
    brute.call("POST", "/api/auth/register", {"username": "brute", "password": pw, "password2": pw})
    codes = [brute.call("POST", "/api/orgs/join", {"code": f"ZZZ-ZZ{i}"})[0] for i in range(2, 9)] + \
            [brute.call("POST", "/api/orgs/join", {"code": f"YYY-YY{i}"})[0] for i in range(2, 9)]
    check("kutsukoodien arvailu estetään (429)", 429 in codes, codes)
    logins = [Client("b").call("POST", "/api/auth/login", {"username": "eve", "password": f"guess-{i}"})[0] for i in range(12)]
    check("salasanan arvailu estetään (429)", logins[-1] == 429, logins)

    # ---------- frontend-polut ----------
    for p in ["/", "/account", f"/org/{oid}", f"/m/{pid}", "/sw.js", "/static/app.js"]:
        check(f"GET {p}", anon.call("GET", p)[0] == 200)
    check("tuntematon API-polku 404", anon.call("GET", "/api/nope")[0] == 404)


def main():
    tmpdir = tempfile.mkdtemp(prefix="coffeenow-test-")
    vapid = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_vapid.py")], capture_output=True, text=True, check=True)
    env = dict(os.environ, COFFEENOW_DB=os.path.join(tmpdir, "test.db"), COOKIE_SECURE="0",
               PUSH_ALLOWED_HOSTS="push.invalid,fcm.googleapis.com", BREW_CHECK_INTERVAL="0.5")
    for line in vapid.stdout.splitlines():
        k, v = line.split("=", 1)
        env[k] = v
    os.environ["COFFEENOW_DB"] = env["COFFEENOW_DB"]

    log = open(os.path.join(tmpdir, "server.log"), "w")
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT)],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(BASE + "/api/config")
                break
            except OSError:
                time.sleep(0.2)
        run()
        print(f"\n{PASSED} testiä OK ☕")
    finally:
        server.terminate()
        server.wait()
        log.close()
        errors = [l for l in open(os.path.join(tmpdir, "server.log")) if "Traceback" in l or "ERROR" in l]
        if errors:
            print("Palvelimen lokissa virheitä:", os.path.join(tmpdir, "server.log"))
            sys.exit(1)


if __name__ == "__main__":
    main()
