"""COFFEE!! ☕ – yhteisöllinen kahvinkeitinseuranta. Analoginen luottamusmalli, digitaalinen kello.

Keitin on joko
- julkinen (org_id NULL): kuka tahansa QR-koodin skannaava voi lukea ja päivittää
- yksityinen (org_id asetettu): vain organisaation jäsenet. Ulkopuolinen ei näe edes nimeä.
"""
import io
import time
import uuid
from pathlib import Path
from typing import Literal

import segno
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, db, orgs, push, security

STATIC = Path(__file__).parent / "static"

WISH_TTL = 2 * 3600          # toive vanhenee 2 tunnissa
RATE_LIMIT_SECONDS = 5       # sama IP ei voi hakata samaa keitintä tätä tiheämmin

Status = Literal["ready", "brewing", "low", "empty", "supplies_low", "supplies_out"]

STATUS_TEXT = {
    "ready": "☕ Kahvia on!",
    "brewing": "⏳ Kahvia tulossa",
    "low": "🫗 Kahvi on vähissä",
    "empty": "🚫 Kahvi loppu",
    "supplies_low": "⚠️ Kahvitarvikkeet vähissä",
    "supplies_out": "🆘 Kahvitarvikkeet loppu",
}

app = FastAPI(title="COFFEE!!", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(auth.router)
app.include_router(orgs.router)
_last_action: dict[tuple[str, str], float] = {}


@app.on_event("startup")
def _startup() -> None:
    db.init()


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
        raise HTTPException(429, "Hetkinen, kahvi ei keity nopeammin painamalla 😉")
    _last_action[key] = now
    if len(_last_action) > 10_000:  # estetään muistin paisuminen
        _last_action.clear()


def _machine_json(con, row, mem) -> dict:
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
    }


# ---------- mallit ----------

class NewMachine(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    org_id: str | None = Field(default=None, max_length=40)


class StatusUpdate(BaseModel):
    status: Status
    cups: int | None = Field(default=None, ge=1, le=100)
    device_id: str = Field(min_length=8, max_length=64)


class DeviceOnly(BaseModel):
    device_id: str = Field(min_length=8, max_length=64)


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=80, max_length=100, pattern=r"^[A-Za-z0-9_-]+=*$")
    auth: str = Field(min_length=16, max_length=40, pattern=r"^[A-Za-z0-9_-]+=*$")


class PushSubscription(BaseModel):
    endpoint: str = Field(max_length=1000, pattern=r"^https://")
    keys: PushKeys


class Follow(BaseModel):
    device_id: str = Field(min_length=8, max_length=64)
    subscription: PushSubscription


class Unfollow(BaseModel):
    endpoint: str = Field(max_length=1000)


# ---------- API ----------

@app.get("/api/config")
def config():
    return {"vapid_public_key": push.VAPID_PUBLIC_KEY, "push_enabled": push.enabled()}


@app.post("/api/machines", status_code=201)
def create_machine(body: NewMachine, request: Request):
    machine_id = str(uuid.uuid4())
    org_id = None
    with db.connect() as con:
        if body.org_id:  # suoraan organisaatioon: vain manageri
            user = security.require_user(request)
            org, mem = orgs._load(con, body.org_id, user)
            orgs._require_manager(mem)
            org_id = org["id"]
        con.execute(
            "INSERT INTO machines (id, name, created_at, org_id) VALUES (?, ?, ?, ?)",
            (machine_id, body.name.strip(), time.time(), org_id),
        )
        row, mem, _ = _load_authorized(con, machine_id, request)
        return _machine_json(con, row, mem)


@app.get("/api/machines/{machine_id}")
def get_machine(machine_id: str, request: Request):
    machine_id = _valid_id(machine_id)
    with db.connect() as con:
        row, mem, _ = _load_authorized(con, machine_id, request)
        return _machine_json(con, row, mem)


@app.post("/api/machines/{machine_id}/status")
def set_status(machine_id: str, body: StatusUpdate, request: Request, bg: BackgroundTasks):
    machine_id = _valid_id(machine_id)
    cups = body.cups if body.status == "brewing" else None
    with db.connect() as con:
        row, mem, _ = _load_authorized(con, machine_id, request)
        _rate_limit(request, machine_id)
        con.execute(
            "UPDATE machines SET status = ?, cups = ?, updated_at = ? WHERE id = ?",
            (body.status, cups, time.time(), machine_id),
        )
        # Kun kahvia on tai on tulossa, toiveet on täytetty -> nollataan
        if body.status in ("ready", "brewing"):
            con.execute("DELETE FROM wishes WHERE machine_id = ?", (machine_id,))
        result = _machine_json(con, _get_machine(con, machine_id), mem)

    text = STATUS_TEXT[body.status]
    if cups:
        text += f" ({cups} kuppia)"
    bg.add_task(push.notify_machine, machine_id, row["name"], text, body.device_id)
    return result


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
        push.notify_machine, machine_id, row["name"],
        f"🙏 Olispa kahvia! ({n} {'toive' if n == 1 else 'toivetta'})", body.device_id,
    )
    return result


@app.post("/api/machines/{machine_id}/follow", status_code=204)
def follow(machine_id: str, body: Follow, request: Request):
    machine_id = _valid_id(machine_id)
    with db.connect() as con:
        _, _, user = _load_authorized(con, machine_id, request)
        s = body.subscription
        con.execute(
            "INSERT OR REPLACE INTO subscriptions (endpoint, machine_id, device_id, p256dh, auth, created_at, user_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (s.endpoint, machine_id, body.device_id, s.keys.p256dh, s.keys.auth, time.time(), user["id"] if user else None),
        )


@app.post("/api/machines/{machine_id}/unfollow", status_code=204)
def unfollow(machine_id: str, body: Unfollow):
    # Ei oikeustarkistusta: endpoint on salaisuus, jonka vain tilauksen omistaja tietää
    machine_id = _valid_id(machine_id)
    with db.connect() as con:
        con.execute(
            "DELETE FROM subscriptions WHERE endpoint = ? AND machine_id = ?", (body.endpoint, machine_id)
        )


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
    return FileResponse(STATIC / "index.html")
