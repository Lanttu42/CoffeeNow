"""COFFEE!! ☕ – yhteisöllinen kahvinkeitinseuranta. Analoginen luottamusmalli, digitaalinen kello."""
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

from . import db, push

STATIC = Path(__file__).parent / "static"

WISH_TTL = 2 * 3600          # toive vanhenee 2 tunnissa
RATE_LIMIT_SECONDS = 5       # sama laite/IP ei voi hakata samaa keitintä tätä tiheämmin

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


def _rate_limit(request: Request, machine_id: str) -> None:
    key = (request.client.host if request.client else "?", machine_id)
    now = time.time()
    if now - _last_action.get(key, 0) < RATE_LIMIT_SECONDS:
        raise HTTPException(429, "Hetkinen, kahvi ei keity nopeammin painamalla 😉")
    _last_action[key] = now
    if len(_last_action) > 10_000:  # estetään muistin paisuminen
        _last_action.clear()


def _machine_json(con, row) -> dict:
    wishes = con.execute(
        "SELECT COUNT(*) FROM wishes WHERE machine_id = ? AND created_at > ?",
        (row["id"], time.time() - WISH_TTL),
    ).fetchone()[0]
    return {
        "id": row["id"],
        "name": row["name"],
        "status": row["status"],
        "cups": row["cups"],
        "updated_at": row["updated_at"],
        "wishes": wishes,
    }


# ---------- mallit ----------

class NewMachine(BaseModel):
    name: str = Field(min_length=1, max_length=60)


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
def create_machine(body: NewMachine):
    machine_id = str(uuid.uuid4())
    with db.connect() as con:
        con.execute(
            "INSERT INTO machines (id, name, created_at) VALUES (?, ?, ?)",
            (machine_id, body.name.strip(), time.time()),
        )
        return _machine_json(con, _get_machine(con, machine_id))


@app.get("/api/machines/{machine_id}")
def get_machine(machine_id: str):
    machine_id = _valid_id(machine_id)
    with db.connect() as con:
        return _machine_json(con, _get_machine(con, machine_id))


@app.post("/api/machines/{machine_id}/status")
def set_status(machine_id: str, body: StatusUpdate, request: Request, bg: BackgroundTasks):
    machine_id = _valid_id(machine_id)
    _rate_limit(request, machine_id)
    cups = body.cups if body.status == "brewing" else None
    with db.connect() as con:
        row = _get_machine(con, machine_id)
        con.execute(
            "UPDATE machines SET status = ?, cups = ?, updated_at = ? WHERE id = ?",
            (body.status, cups, time.time(), machine_id),
        )
        # Kun kahvia on tai on tulossa, toiveet on täytetty -> nollataan
        if body.status in ("ready", "brewing"):
            con.execute("DELETE FROM wishes WHERE machine_id = ?", (machine_id,))
        result = _machine_json(con, _get_machine(con, machine_id))

    text = STATUS_TEXT[body.status]
    if cups:
        text += f" ({cups} kuppia)"
    bg.add_task(push.notify_machine, machine_id, row["name"], text, body.device_id)
    return result


@app.post("/api/machines/{machine_id}/wish")
def wish(machine_id: str, body: DeviceOnly, request: Request, bg: BackgroundTasks):
    machine_id = _valid_id(machine_id)
    _rate_limit(request, machine_id)
    with db.connect() as con:
        row = _get_machine(con, machine_id)
        con.execute(
            "INSERT OR REPLACE INTO wishes (machine_id, device_id, created_at) VALUES (?, ?, ?)",
            (machine_id, body.device_id, time.time()),
        )
        result = _machine_json(con, row)

    n = result["wishes"]
    bg.add_task(
        push.notify_machine, machine_id, row["name"],
        f"🙏 Olispa kahvia! ({n} {'toive' if n == 1 else 'toivetta'})", body.device_id,
    )
    return result


@app.post("/api/machines/{machine_id}/follow", status_code=204)
def follow(machine_id: str, body: Follow):
    machine_id = _valid_id(machine_id)
    with db.connect() as con:
        _get_machine(con, machine_id)
        s = body.subscription
        con.execute(
            "INSERT OR REPLACE INTO subscriptions (endpoint, machine_id, device_id, p256dh, auth, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (s.endpoint, machine_id, body.device_id, s.keys.p256dh, s.keys.auth, time.time()),
        )


@app.post("/api/machines/{machine_id}/unfollow", status_code=204)
def unfollow(machine_id: str, body: Unfollow):
    machine_id = _valid_id(machine_id)
    with db.connect() as con:
        con.execute(
            "DELETE FROM subscriptions WHERE endpoint = ? AND machine_id = ?", (body.endpoint, machine_id)
        )


@app.get("/api/machines/{machine_id}/qr.svg")
def qr(machine_id: str, request: Request):
    machine_id = _valid_id(machine_id)
    with db.connect() as con:
        _get_machine(con, machine_id)
    url = f"{request.base_url}m/{machine_id}"
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=8, border=2)
    return Response(buf.getvalue(), media_type="image/svg+xml")


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
    # Kaikki muut polut (/, /new, /m/<uuid>) -> sama yksisivuinen sovellus
    if path.startswith("api/"):
        raise HTTPException(404)
    return FileResponse(STATIC / "index.html")
