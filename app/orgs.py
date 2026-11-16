"""Organisaatiot: yksityiset kahvinkeitinporukat kutsukoodilla.

Roolit:
- perustaja (founder): poistaa organisaation, nimeää ylläpitäjät, poistaa jäseniä, muuttaa asetuksia
- ylläpitäjä (manager): liittää keittimiä organisaatioon, vaihtaa kutsukoodin
- jäsen: näkee ja päivittää organisaation keittimiä
"""
import sqlite3
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from . import db, security

router = APIRouter(prefix="/api/orgs")

join_user_limiter = security.Limiter(10, 600, "Liikaa vääriä kutsukoodeja. Odota 10 minuuttia.")
join_ip_limiter = security.Limiter(50, 600, "Liikaa vääriä kutsukoodeja. Odota 10 minuuttia.")
create_org_limiter = security.Limiter(10, 3600, "Liian monta uutta organisaatiota. Yritä myöhemmin.")


class NewOrg(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    auto_manager: bool = False

    @field_validator("name")
    @classmethod
    def _clean(cls, v: str) -> str:
        return security.clean_name(v)


class JoinOrg(BaseModel):
    code: str = Field(max_length=20)


class Settings(BaseModel):
    auto_manager: bool


class ManagerFlag(BaseModel):
    is_manager: bool


class ConfirmDelete(BaseModel):
    confirm_name: str = Field(max_length=60)


class LinkMachine(BaseModel):
    machine_id: str = Field(max_length=40)
    device_id: str | None = Field(default=None, min_length=8, max_length=64)


# ---------- apurit ----------

def _valid_uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise HTTPException(404, "Ei löydy")


def _load(con, org_id: str, user: dict):
    """Palauttaa (org, membership). Ulkopuoliselle 404 -> organisaation olemassaoloa ei paljasteta."""
    org_id = _valid_uuid(org_id)
    org = con.execute("SELECT * FROM orgs WHERE id = ?", (org_id,)).fetchone()
    mem = con.execute(
        "SELECT * FROM memberships WHERE org_id = ? AND user_id = ?", (org_id, user["id"])
    ).fetchone() if org else None
    if not org or not mem:
        raise HTTPException(404, "Organisaatiota ei löydy")
    return org, mem


def _require_manager(mem) -> None:
    if not mem["is_manager"]:
        raise HTTPException(403, "Vain ylläpitäjä voi tehdä tämän")


def _require_founder(org, user) -> None:
    if org["founder_id"] != user["id"]:
        raise HTTPException(403, "Vain perustaja voi tehdä tämän")


def drop_foreign_subscriptions(con, org_id: str, machine_id: str | None = None) -> None:
    """Poistaa organisaation keittimiltä push-tilaukset, joiden omistaja ei ole jäsen."""
    sql = (
        "DELETE FROM subscriptions WHERE machine_id IN (SELECT id FROM machines WHERE org_id = ?)"
        " AND (user_id IS NULL OR user_id NOT IN (SELECT user_id FROM memberships WHERE org_id = ?))"
    )
    params: tuple = (org_id, org_id)
    if machine_id:
        sql += " AND machine_id = ?"
        params += (machine_id,)
    con.execute(sql, params)


# ---------- API ----------

@router.post("", status_code=201)
def create_org(body: NewOrg, request: Request, user: dict = Depends(security.require_user)):
    ip = security.client_ip(request)
    create_org_limiter.check(ip)
    create_org_limiter.hit(ip)
    org_id = str(uuid.uuid4())
    now = time.time()
    with db.connect() as con:
        for _ in range(5):  # kutsukoodin törmäys on epätodennäköinen, mutta varaudutaan
            try:
                con.execute(
                    "INSERT INTO orgs (id, name, invite_code, founder_id, auto_manager, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (org_id, body.name, security.new_invite_code(), user["id"], int(body.auto_manager), now),
                )
                break
            except sqlite3.IntegrityError:
                continue
        con.execute(
            "INSERT INTO memberships (org_id, user_id, is_manager, joined_at) VALUES (?, ?, 1, ?)", (org_id, user["id"], now)
        )
    return {"id": org_id}


@router.post("/join")
def join_org(body: JoinOrg, request: Request, user: dict = Depends(security.require_user)):
    ip, ukey = security.client_ip(request), f"user:{user['id']}"
    join_ip_limiter.check(ip)
    join_user_limiter.check(ukey)
    code = security.normalize_invite_code(body.code)
    with db.connect() as con:
        org = con.execute("SELECT id, name, auto_manager FROM orgs WHERE invite_code = ?", (code,)).fetchone() if code else None
        if not org:
            join_ip_limiter.hit(ip)
            join_user_limiter.hit(ukey)
            raise HTTPException(404, "Kutsukoodia ei löydy")
        con.execute(
            "INSERT OR IGNORE INTO memberships (org_id, user_id, is_manager, joined_at) VALUES (?, ?, ?, ?)",
            (org["id"], user["id"], org["auto_manager"], time.time()),
        )
    return {"id": org["id"], "name": org["name"]}


@router.get("/{org_id}")
def get_org(org_id: str, user: dict = Depends(security.require_user)):
    with db.connect() as con:
        org, mem = _load(con, org_id, user)
        members = con.execute(
            "SELECT u.id, u.username, m.is_manager FROM memberships m JOIN users u ON u.id = m.user_id"
            " WHERE m.org_id = ? ORDER BY m.is_manager DESC, u.username COLLATE NOCASE",
            (org["id"],),
        ).fetchall()
        machines = con.execute(
            "SELECT id, name, status, cups, updated_at FROM machines WHERE org_id = ? ORDER BY name COLLATE NOCASE",
            (org["id"],),
        ).fetchall()
    return {
        "id": org["id"],
        "name": org["name"],
        "invite_code": org["invite_code"],
        "auto_manager": bool(org["auto_manager"]),
        "is_manager": bool(mem["is_manager"]),
        "is_founder": org["founder_id"] == user["id"],
        "members": [
            {"id": m["id"], "username": m["username"], "is_manager": bool(m["is_manager"]), "is_founder": m["id"] == org["founder_id"]}
            for m in members
        ],
        "machines": [dict(m) for m in machines],
    }


@router.post("/{org_id}/settings", status_code=204)
def update_settings(org_id: str, body: Settings, user: dict = Depends(security.require_user)):
    with db.connect() as con:
        org, _ = _load(con, org_id, user)
        _require_founder(org, user)
        con.execute("UPDATE orgs SET auto_manager = ? WHERE id = ?", (int(body.auto_manager), org["id"]))


@router.post("/{org_id}/invite/rotate")
def rotate_invite(org_id: str, user: dict = Depends(security.require_user)):
    with db.connect() as con:
        org, mem = _load(con, org_id, user)
        _require_manager(mem)
        code = security.new_invite_code()
        con.execute("UPDATE orgs SET invite_code = ? WHERE id = ?", (code, org["id"]))
    return {"invite_code": code}


@router.post("/{org_id}/members/{member_id}/manager", status_code=204)
def set_manager(org_id: str, member_id: int, body: ManagerFlag, user: dict = Depends(security.require_user)):
    with db.connect() as con:
        org, _ = _load(con, org_id, user)
        _require_founder(org, user)
        if member_id == org["founder_id"]:
            raise HTTPException(400, "Perustaja on aina ylläpitäjä")
        cur = con.execute(
            "UPDATE memberships SET is_manager = ? WHERE org_id = ? AND user_id = ?", (int(body.is_manager), org["id"], member_id)
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Jäsentä ei löydy")


@router.post("/{org_id}/members/{member_id}/remove", status_code=204)
def remove_member(org_id: str, member_id: int, user: dict = Depends(security.require_user)):
    with db.connect() as con:
        org, _ = _load(con, org_id, user)
        _require_founder(org, user)
        if member_id == org["founder_id"]:
            raise HTTPException(400, "Perustajaa ei voi poistaa. Poista koko organisaatio.")
        con.execute("DELETE FROM memberships WHERE org_id = ? AND user_id = ?", (org["id"], member_id))
        drop_foreign_subscriptions(con, org["id"])


@router.post("/{org_id}/leave", status_code=204)
def leave_org(org_id: str, user: dict = Depends(security.require_user)):
    with db.connect() as con:
        org, _ = _load(con, org_id, user)
        if org["founder_id"] == user["id"]:
            raise HTTPException(400, "Perustaja ei voi erota. Poista organisaatio, jos haluat lopettaa.")
        con.execute("DELETE FROM memberships WHERE org_id = ? AND user_id = ?", (org["id"], user["id"]))
        drop_foreign_subscriptions(con, org["id"])


@router.post("/{org_id}/delete", status_code=204)
def delete_org(org_id: str, body: ConfirmDelete, user: dict = Depends(security.require_user)):
    with db.connect() as con:
        org, _ = _load(con, org_id, user)
        _require_founder(org, user)
        if body.confirm_name.strip() != org["name"]:
            raise HTTPException(400, "Kirjoita organisaation nimi täsmälleen vahvistukseksi")
        con.execute("DELETE FROM orgs WHERE id = ?", (org["id"],))  # CASCADE: jäsenyydet, keittimet, tilaukset


@router.post("/{org_id}/machines", status_code=204)
def link_machine(org_id: str, body: LinkMachine, user: dict = Depends(security.require_user)):
    machine_id = _valid_uuid(body.machine_id)
    with db.connect() as con:
        org, mem = _load(con, org_id, user)
        _require_manager(mem)
        m = con.execute(
            "SELECT org_id, creator_user_id, creator_device_hash FROM machines WHERE id = ?", (machine_id,)
        ).fetchone()
        if not m:
            raise HTTPException(404, "Kahvinkeitintä ei löydy")
        if m["org_id"] == org["id"]:
            return
        if m["org_id"]:
            raise HTTPException(409, "Keitin kuuluu jo toiseen organisaatioon")
        # Julkisen keittimen saa yksityistää vain sen luoja. Muuten kuka tahansa QR-koodin nähnyt
        # voisi viedä toimiston yhteisen keittimen omaan porukkaansa.
        own = (m["creator_user_id"] is not None and m["creator_user_id"] == user["id"]) or (
            body.device_id and m["creator_device_hash"] == security.hash_device(body.device_id))
        if not own:
            raise HTTPException(403, "Vain keittimen luoja voi liittää sen organisaatioon")
        con.execute("UPDATE machines SET org_id = ? WHERE id = ?", (org["id"], machine_id))
        con.execute("DELETE FROM wishes WHERE machine_id = ?", (machine_id,))
        drop_foreign_subscriptions(con, org["id"], machine_id)


@router.post("/{org_id}/machines/{machine_id}/unlink", status_code=204)
def unlink_machine(org_id: str, machine_id: str, user: dict = Depends(security.require_user)):
    machine_id = _valid_uuid(machine_id)
    with db.connect() as con:
        org, mem = _load(con, org_id, user)
        _require_manager(mem)
        cur = con.execute("UPDATE machines SET org_id = NULL WHERE id = ? AND org_id = ?", (machine_id, org["id"]))
        if cur.rowcount == 0:
            raise HTTPException(404, "Keitin ei kuulu tähän organisaatioon")
