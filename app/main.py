"""COFFEE!! – yhteisöllinen kahvinkeitinseuranta.

Keitin on joko
- julkinen (org_id NULL): kuka tahansa QR-koodin skannaava voi lukea ja päivittää
- yksityinen (org_id asetettu): vain organisaation jäsenet. Ulkopuolinen ei näe edes nimeä.
"""
import asyncio
import hashlib
import io
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Literal

import segno
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, Field, field_validator

from . import auth, db, i18n, notify, orgs, push, security

STATIC = Path(__file__).parent / "static"
log = logging.getLogger("coffeenow")

# Versio: VERSION näytetään käyttäjälle, BUILD (hash staattisista tiedostoista) muuttuu aina kun frontend muuttuu.
# Selain vertaa lataamaansa BUILDia palvelimen BUILDiin ja pyytää päivittämään, jos ne eroavat.
VERSION = (Path(__file__).parent / "VERSION").read_text().strip()


def _build_id() -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in STATIC.rglob("*") if p.is_file()):
        h.update(f.relative_to(STATIC).as_posix().encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:10]


BUILD = _build_id()
# index.html: tiedostojen osoitteisiin ?v=BUILD (välimuisti ei tarjoa vanhoja) + versiotiedot meta-tageihin
INDEX_HTML = ((STATIC / "index.html").read_text()
              .replace("{{VERSION}}", VERSION).replace("{{BUILD}}", BUILD))

WISH_TTL = 2 * 3600          # toive vanhenee 2 tunnissa
BREW_COOLDOWN = 16 * 60     # 2 x Moccamasterin ~8 min keittoaika; sama käyttäjä saa samasta keittimestä enintään yhden pannun tässä ajassa
BREW_CANCEL_WINDOW = 5 * 60  # loppu/vähissä tätä nopeammin pannun jälkeen -> pannu mitätöidään
TOP_N = 10
DEFAULT_FULL_POT = 10       # kuppia; keitinkohtainen asetus machines.full_pot_cups
MAX_CUPS = 999
DEFAULT_BREW_MIN_PER_CUP = 1.0   # keittoaika min/kuppi; keitinkohtainen asetus machines.brew_min_per_cup
MAX_BREW_MIN_PER_CUP = 30.0
# Oletuskahvityypit (suomenkieliset avaimet, käännökset i18n:ssä). Keitinkohtainen lista: machines.coffee_types
DEFAULT_COFFEE_TYPES = ["Tavallinen", "Tumma paahto", "Kofeiiniton"]
MAX_COFFEE_TYPES = 8
BREW_CHECK_INTERVAL = float(os.environ.get("BREW_CHECK_INTERVAL", 15))  # "tulossa" -> "on" -tarkistuksen väli (s)
RATE_LIMIT_SECONDS = 5       # sama IP ei voi hakata samaa keitintä tätä tiheämmin

Status = Literal["ready", "brewing", "low", "empty", "supplies_low", "supplies_out"]

STATUS_TEXT = {  # käännökset: i18n.MESSAGES
    "ready": "Kahvia on!",
    "brewing": "Kahvia tulossa",
    "low": "Kahvi vähissä",
    "empty": "Kahvi loppu",
    "supplies_low": "Kahvitarvikkeet vähissä",
    "supplies_out": "Kahvitarvikkeet loppu",
}

app = FastAPI(title="COFFEE!!", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(auth.router)
app.include_router(orgs.router)
_last_action: dict[tuple[str, str], float] = {}
create_limiter = security.Limiter(20, 3600, "Liian monta uutta keitintä tästä osoitteesta. Yritä myöhemmin.")
follow_limiter = security.Limiter(60, 3600, "Liian monta tilausta tästä osoitteesta. Yritä myöhemmin.")


_brew_task: asyncio.Task | None = None


@app.on_event("startup")
async def _startup() -> None:
    global _brew_task
    db.init()
    _brew_task = asyncio.create_task(_brew_loop())


@app.on_event("shutdown")
async def _shutdown() -> None:
    if _brew_task:
        _brew_task.cancel()


@app.exception_handler(StarletteHTTPException)
async def _translated_error(request: Request, exc: StarletteHTTPException):
    """Virheviestit käyttäjän kielellä. Koodit (login_required, not_member) jätetään ennalleen."""
    detail = exc.detail
    if isinstance(detail, str):
        detail = i18n.tr(detail, i18n.lang_of(request))
    return JSONResponse({"detail": detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


# ---------- apufunktiot ----------

def _valid_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise HTTPException(404, "Kahvinkeitintä ei löydy")


def _get_machine(con, machine_id: str):
    row = con.execute("SELECT * FROM machines WHERE id = ?", (machine_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Kahvinkeitintä ei löydy")
    return row


def _authorize(con, row, user: dict | None):
    """Julkinen keitin -> None. Yksityinen -> jäsenyysrivi, tai 401/403 paljastamatta organisaation nimeä."""
    if not row["org_id"]:
        return None
    if not user:
        raise HTTPException(401, "login_required")
    mem = con.execute(
        "SELECT is_manager FROM memberships WHERE org_id = ? AND user_id = ?", (row["org_id"], user["id"])
    ).fetchone()
    if not mem:
        raise HTTPException(403, "not_member")
    return mem


def _load_authorized(con, machine_id: str, request: Request):
    user = security.current_user(request)
    row = _get_machine(con, machine_id)
    mem = _authorize(con, row, user)
    return row, mem, user


def _rate_limit(request: Request, machine_id: str) -> None:
    key = (security.client_ip(request), machine_id)
    now = time.time()
    if now - _last_action.get(key, 0) < RATE_LIMIT_SECONDS:
        raise HTTPException(429, "Hetkinen, kahvi ei keity nopeammin painamalla.")
    _last_action[key] = now
    if len(_last_action) > 10_000:  # estetään muistin paisuminen
        _last_action.clear()


def _can_edit(row, mem, user, request: Request | None) -> bool:
    """Keittimen asetukset: organisaation keittimessä ylläpitäjät, julkisessa luoja (tunnus tai laite)."""
    if row["org_id"]:
        return bool(mem and mem["is_manager"])
    if user and row["creator_user_id"] is not None and row["creator_user_id"] == user["id"]:
        return True
    device = request.headers.get("x-device-id", "") if request else ""
    return bool(8 <= len(device) <= 64 and row["creator_device_hash"] == security.hash_device(device))


def _coffee_types(row) -> list[str]:
    try:
        types = json.loads(row["coffee_types"]) if row["coffee_types"] else None
    except ValueError:
        types = None
    return types if isinstance(types, list) and types else list(DEFAULT_COFFEE_TYPES)


def _brew_min_per_cup(row) -> float:
    v = row["brew_min_per_cup"]
    return DEFAULT_BREW_MIN_PER_CUP if v is None else float(v)


def _details(cups: int | None, coffee_type: str | None, estimated: bool, lang: str) -> str:
    """ " (10 kuppia, Tumma paahto) (ehkä)" käyttäjän kielellä."""
    parts = []
    if cups:
        parts.append(i18n.tr("{n} kuppia", lang, n=cups))
    if coffee_type:
        parts.append(i18n.tr(coffee_type, lang))
    out = f" ({', '.join(parts)})" if parts else ""
    return out + (i18n.tr(" (ehkä)", lang) if estimated and out else "")


def _machine_json(con, row, mem, user=None, request: Request | None = None) -> dict:
    wishes = con.execute(
        "SELECT COUNT(*) FROM wishes WHERE machine_id = ? AND created_at > ?",
        (row["id"], time.time() - WISH_TTL),
    ).fetchone()[0]
    org = None
    if row["org_id"]:
        o = con.execute("SELECT id, name FROM orgs WHERE id = ?", (row["org_id"],)).fetchone()
        org = {"id": o["id"], "name": o["name"], "is_manager": bool(mem and mem["is_manager"])}
    return {
        "id": row["id"],
        "name": row["name"],
        "status": row["status"],
        "cups": row["cups"],
        "updated_at": row["updated_at"],
        "wishes": wishes,
        "org": org,
        "full_pot_cups": row["full_pot_cups"] or DEFAULT_FULL_POT,
        "brew_type": row["brew_type"],
        "estimated": bool(row["brew_estimated"]),
        "brew_ready_at": row["brew_ready_at"],
        "last_brew_type": row["last_brew_type"],
        "last_brew_cups": row["last_brew_cups"],
        "brew_min_per_cup": _brew_min_per_cup(row),
        "coffee_types": _coffee_types(row),
        "can_edit": _can_edit(row, mem, user, request),
    }


# ---------- mallit ----------

class NewMachine(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    org_id: str | None = Field(default=None, max_length=40)
    device_id: str | None = Field(default=None, min_length=8, max_length=64)

    @field_validator("name")
    @classmethod
    def _clean(cls, v: str) -> str:
        return security.clean_name(v)


class StatusUpdate(BaseModel):
    status: Status
    cups: int | None = Field(default=None, ge=1, le=MAX_CUPS)
    coffee_type: str | None = Field(default=None, max_length=60)
    quick: bool = False      # pikailmoitus (pyyhkäisy): kuppimäärä ja tyyppi edellisestä pannusta, merkitään "ehkä"
    device_id: str = Field(min_length=8, max_length=64)


class MachineSettings(BaseModel):
    """Kaikki kentät valinnaisia: puuttuva = ennallaan."""
    full_pot_cups: int | None = Field(default=None, ge=1, le=MAX_CUPS)
    brew_min_per_cup: float | None = Field(default=None, ge=0, le=MAX_BREW_MIN_PER_CUP)
    coffee_types: list[str] | None = Field(default=None, min_length=1, max_length=MAX_COFFEE_TYPES)

    @field_validator("coffee_types")
    @classmethod
    def _clean_types(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        out: list[str] = []
        for name in v:
            name = security.clean_name(name)
            if len(name) > 30:
                raise ValueError("Kahvityypin nimi saa olla enintään 30 merkkiä")
            if name.lower() not in (o.lower() for o in out):
                out.append(name)
        return out


class DeviceOnly(BaseModel):
    device_id: str = Field(min_length=8, max_length=64)


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=80, max_length=100, pattern=r"^[A-Za-z0-9_-]+=*$")
    auth: str = Field(min_length=16, max_length=40, pattern=r"^[A-Za-z0-9_-]+=*$")


class PushSubscription(BaseModel):
    endpoint: str = Field(max_length=1000, pattern=r"^https://")
    keys: PushKeys

    @field_validator("endpoint")
    @classmethod
    def _known_push_service(cls, v: str) -> str:
        if not push.endpoint_allowed(v):
            raise ValueError("Tuntematon push-palvelu")
        return v


class Follow(BaseModel):
    device_id: str = Field(min_length=8, max_length=64)
    subscription: PushSubscription


class Unfollow(BaseModel):
    endpoint: str = Field(max_length=1000)


class Mute(BaseModel):
    endpoint: str = Field(max_length=1000)
    minutes: Literal[60, 240, 1440] | None = None   # None = toistaiseksi


# ---------- API ----------

@app.get("/api/config")
def config():
    return {"vapid_public_key": push.VAPID_PUBLIC_KEY, "push_enabled": push.enabled(),
            "version": VERSION, "build": BUILD}


@app.post("/api/machines", status_code=201)
def create_machine(body: NewMachine, request: Request):
    ip = security.client_ip(request)
    create_limiter.check(ip)
    machine_id = str(uuid.uuid4())
    org_id = None
    user = security.current_user(request)
    with db.connect() as con:
        if body.org_id:  # suoraan organisaatioon: vain ylläpitäjä
            if not user:
                raise HTTPException(401, "login_required")
            org, mem = orgs._load(con, body.org_id, user)
            orgs._require_manager(mem)
            org_id = org["id"]
        con.execute(
            "INSERT INTO machines (id, name, created_at, org_id, creator_user_id, creator_device_hash)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (machine_id, body.name, time.time(), org_id, user["id"] if user else None,
             security.hash_device(body.device_id) if body.device_id else None),
        )
    create_limiter.hit(ip)
    with db.connect() as con:
        row, mem, _ = _load_authorized(con, machine_id, request)
        return _machine_json(con, row, mem)


@app.get("/api/machines/{machine_id}")
def get_machine(machine_id: str, request: Request):
    machine_id = _valid_id(machine_id)
    with db.connect() as con:
        row, mem, user = _load_authorized(con, machine_id, request)
        return _machine_json(con, row, mem, user, request)


@app.post("/api/machines/{machine_id}/settings")
def machine_settings(machine_id: str, body: MachineSettings, request: Request):
    machine_id = _valid_id(machine_id)
    with db.connect() as con:
        row, mem, user = _load_authorized(con, machine_id, request)
        if not _can_edit(row, mem, user, request):
            raise HTTPException(403, "Vain keittimen luoja tai ylläpitäjä voi muuttaa asetuksia")
        if body.full_pot_cups is not None:
            con.execute("UPDATE machines SET full_pot_cups = ? WHERE id = ?", (body.full_pot_cups, machine_id))
        if body.brew_min_per_cup is not None:
            # puolen minuutin tarkkuus riittää
            con.execute("UPDATE machines SET brew_min_per_cup = ? WHERE id = ?",
                        (round(body.brew_min_per_cup * 2) / 2, machine_id))
        if body.coffee_types is not None:
            con.execute("UPDATE machines SET coffee_types = ? WHERE id = ?",
                        (json.dumps(body.coffee_types, ensure_ascii=False), machine_id))
        return _machine_json(con, _get_machine(con, machine_id), mem, user, request)


@app.post("/api/machines/{machine_id}/status")
def set_status(machine_id: str, body: StatusUpdate, request: Request, bg: BackgroundTasks):
    machine_id = _valid_id(machine_id)
    with db.connect() as con:
        row, mem, user = _load_authorized(con, machine_id, request)
        _rate_limit(request, machine_id)
        now = time.time()
        status, cups, ctype, estimated, ready_at = body.status, None, None, False, None
        if body.status == "brewing":
            types = _coffee_types(row)
            if body.quick:
                # Pikailmoitus: edellisen pannun arvot, merkitään arvioiduksi
                cups = row["last_brew_cups"] or row["full_pot_cups"] or DEFAULT_FULL_POT
                ctype = row["last_brew_type"] if row["last_brew_type"] in types else types[0]
                estimated = True
            else:
                cups = body.cups or row["last_brew_cups"] or row["full_pot_cups"] or DEFAULT_FULL_POT
                ctype = body.coffee_type.strip() if body.coffee_type else None
                if ctype and ctype not in types:
                    raise HTTPException(400, "Tuntematon kahvityyppi")
            ready_at = now + cups * _brew_min_per_cup(row) * 60
            con.execute("UPDATE machines SET last_brew_cups = ?, last_brew_type = COALESCE(?, last_brew_type) WHERE id = ?",
                        (cups, ctype, machine_id))
            if ready_at <= now:  # keittoaika 0: kahvi on heti valmista
                status, ready_at = "ready", None
        elif body.status == "ready" and row["status"] == "brewing":
            # "tulossa" -> "on" käsin: pannun tiedot säilyvät
            cups, ctype, estimated = row["cups"], row["brew_type"], bool(row["brew_estimated"])
        con.execute(
            "UPDATE machines SET status = ?, cups = ?, updated_at = ?, brew_type = ?, brew_estimated = ?, brew_ready_at = ?"
            " WHERE id = ?",
            (status, cups, now, ctype, int(estimated), ready_at, machine_id),
        )
        # Pannu kirjautuneelle: "tulossa", tai "on" ilman edeltävää "tulossa"-ilmoitusta (keitti ilmoittamatta)
        brewed = body.status == "brewing" or (body.status == "ready" and row["status"] != "brewing")
        if user and brewed and not con.execute(
            "SELECT 1 FROM brews WHERE machine_id = ? AND user_id = ? AND created_at > ?",
            (machine_id, user["id"], now - BREW_COOLDOWN),
        ).fetchone():
            con.execute("INSERT INTO brews (machine_id, user_id, created_at) VALUES (?, ?, ?)", (machine_id, user["id"], now))
        # Grindauksen esto: jos kahvi on "loppu" tai "vähissä" alle 5 min pannun jälkeen, pannua tuskin keitettiin
        if body.status in ("empty", "low"):
            con.execute("DELETE FROM brews WHERE machine_id = ? AND created_at > ?", (machine_id, now - BREW_CANCEL_WINDOW))
        # Kun kahvia on tai on tulossa, toiveet on täytetty -> nollataan
        if body.status in ("ready", "brewing"):
            con.execute("DELETE FROM wishes WHERE machine_id = ?", (machine_id,))
        result = _machine_json(con, _get_machine(con, machine_id), mem)

    def text(lang: str) -> str:
        t = i18n.tr(STATUS_TEXT[status], lang) + _details(cups, ctype, estimated, lang)
        if status == "brewing" and ready_at:
            t += i18n.tr(" – valmis noin {n} min päästä.", lang, n=max(1, round((ready_at - now) / 60)))
        return t

    bg.add_task(notify.status_changed, machine_id, status, row["name"], text, body.device_id)
    return result


# ---------- "tulossa" -> "on" keittoajan jälkeen ----------

def _finish_due_brews() -> list:
    """Siirtää valmistuneet pannut "on"-tilaan. UPDATE ehdollinen, joten sama pannu valmistuu vain kerran."""
    now = time.time()
    done = []
    with db.connect() as con:
        rows = con.execute(
            "SELECT id, name, cups, brew_type, brew_estimated, brew_ready_at FROM machines"
            " WHERE status = 'brewing' AND brew_ready_at IS NOT NULL AND brew_ready_at <= ?", (now,)
        ).fetchall()
        for r in rows:
            cur = con.execute(
                "UPDATE machines SET status = 'ready', updated_at = ?, brew_ready_at = NULL"
                " WHERE id = ? AND status = 'brewing' AND brew_ready_at = ?",
                (r["brew_ready_at"], r["id"], r["brew_ready_at"]),
            )
            if cur.rowcount:
                done.append(dict(r))
    return done


async def finish_brews_once() -> int:
    finished = await run_in_threadpool(_finish_due_brews)
    for r in finished:
        def text(lang: str, r=r) -> str:
            return i18n.tr("Kahvi on valmista!", lang) + _details(r["cups"], r["brew_type"], bool(r["brew_estimated"]), lang)
        await notify.status_changed(r["id"], "ready", r["name"], text, None)
    return len(finished)


async def _brew_loop() -> None:
    while True:
        try:
            await finish_brews_once()
        except Exception:  # yksittäinen virhe ei saa pysäyttää silmukkaa
            log.exception("Pannujen valmistumisen tarkistus epäonnistui")
        await asyncio.sleep(BREW_CHECK_INTERVAL)


@app.get("/api/machines/{machine_id}/top")
def top_brewers(machine_id: str, request: Request):
    machine_id = _valid_id(machine_id)
    with db.connect() as con:
        _, _, user = _load_authorized(con, machine_id, request)
        rows = con.execute(
            "SELECT u.username, COUNT(*) AS brews, MAX(b.created_at) AS last FROM brews b JOIN users u ON u.id = b.user_id"
            " WHERE b.machine_id = ? GROUP BY b.user_id ORDER BY brews DESC, last ASC LIMIT ?",
            (machine_id, TOP_N),
        ).fetchall()
    return {"top": [{"username": r["username"], "brews": r["brews"]} for r in rows],
            "me": user["username"] if user else None}


@app.post("/api/machines/{machine_id}/wish")
def wish(machine_id: str, body: DeviceOnly, request: Request, bg: BackgroundTasks):
    machine_id = _valid_id(machine_id)
    with db.connect() as con:
        row, mem, _ = _load_authorized(con, machine_id, request)
        _rate_limit(request, machine_id)
        con.execute(
            "INSERT OR REPLACE INTO wishes (machine_id, device_id, created_at) VALUES (?, ?, ?)",
            (machine_id, body.device_id, time.time()),
        )
        result = _machine_json(con, row, mem)

    n = result["wishes"]
    bg.add_task(
        notify.wished, machine_id, row["name"],
        lambda lang: i18n.tr("Joku toivoo kahvia." if n == 1 else "{n} ihmistä toivoo kahvia.", lang, n=n),
        body.device_id,
    )
    return result


@app.post("/api/machines/{machine_id}/follow", status_code=204)
def follow(machine_id: str, body: Follow, request: Request):
    machine_id = _valid_id(machine_id)
    ip = security.client_ip(request)
    follow_limiter.check(ip)
    with db.connect() as con:
        _, _, user = _load_authorized(con, machine_id, request)
        follow_limiter.hit(ip)
        s = body.subscription
        con.execute(
            "INSERT OR REPLACE INTO subscriptions (endpoint, machine_id, device_id, p256dh, auth, created_at, user_id, lang)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (s.endpoint, machine_id, body.device_id, s.keys.p256dh, s.keys.auth, time.time(),
             user["id"] if user else None, i18n.lang_of(request)),
        )


@app.post("/api/machines/{machine_id}/unfollow", status_code=204)
def unfollow(machine_id: str, body: Unfollow):
    # Ei oikeustarkistusta: endpoint on salaisuus, jonka vain tilauksen omistaja tietää
    machine_id = _valid_id(machine_id)
    with db.connect() as con:
        con.execute(
            "DELETE FROM subscriptions WHERE endpoint = ? AND machine_id = ?", (body.endpoint, machine_id)
        )


# ---------- hiljennys (laitekohtainen: push-endpoint on laitteen salaisuus) ----------

MUTE_FOREVER = 4102444800.0  # 2100-01-01


def _require_subscription(con, endpoint: str) -> None:
    if not con.execute("SELECT 1 FROM subscriptions WHERE endpoint = ?", (endpoint,)).fetchone():
        raise HTTPException(404, "Ilmoitustilausta ei löydy")


@app.post("/api/push/mute")
def mute(body: Mute):
    until = time.time() + body.minutes * 60 if body.minutes else MUTE_FOREVER
    with db.connect() as con:
        _require_subscription(con, body.endpoint)
        con.execute("INSERT OR REPLACE INTO push_mutes (endpoint, until) VALUES (?, ?)", (body.endpoint, until))
    return {"until": None if until == MUTE_FOREVER else until}


@app.post("/api/push/unmute", status_code=204)
def unmute(body: Unfollow):
    with db.connect() as con:
        con.execute("DELETE FROM push_mutes WHERE endpoint = ?", (body.endpoint,))


@app.get("/api/machines/{machine_id}/qr.svg")
def qr(machine_id: str, request: Request):
    machine_id = _valid_id(machine_id)
    with db.connect() as con:
        _load_authorized(con, machine_id, request)
    # Linkki sisältää vain keittimen UUID:n, ei organisaatiota
    url = f"{request.base_url}m/{machine_id}"
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=8, border=2)
    return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "private, no-store"})


# ---------- frontend ----------

@app.get("/sw.js")
def service_worker():
    # Service worker juuresta, jotta sen scope kattaa koko sovelluksen
    return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/manifest.json")
def manifest():
    return FileResponse(STATIC / "manifest.json", media_type="application/manifest+json")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/{path:path}")
def spa(path: str):
    # Kaikki muut polut (/, /new, /m/<uuid>, /account, /org/<uuid>) -> sama yksisivuinen sovellus
    if path.startswith("api/"):
        raise HTTPException(404)
    return HTMLResponse(INDEX_HTML, headers={"Cache-Control": "no-cache"})
