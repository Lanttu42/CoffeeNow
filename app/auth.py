"""Anonyymit tunnukset: käyttäjätunnus + salasana. Ei sähköpostia -> ei salasanan palautusta."""
import re
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import db, security

router = APIRouter(prefix="/api/auth")

USERNAME_RE = re.compile(r"^[A-Za-z0-9ÅÄÖåäö_.-]{3,32}$")
MIN_PASSWORD = 10

# Toimistossa kaikki voivat näkyä samana IP:nä (NAT) -> IP-rajat väljempiä kuin tunnuskohtaiset
login_user_limiter = security.Limiter(10, 600, "Liikaa kirjautumisyrityksiä. Odota 10 minuuttia ☕")
login_ip_limiter = security.Limiter(50, 600, "Liikaa kirjautumisyrityksiä. Odota 10 minuuttia ☕")
register_limiter = security.Limiter(30, 3600, "Liikaa uusia tunnuksia tästä osoitteesta. Yritä myöhemmin.")


def _login_check(ip: str, ukey: str) -> None:
    login_ip_limiter.check(ip)
    login_user_limiter.check(ukey)


def _login_fail(ip: str, ukey: str) -> None:
    login_ip_limiter.hit(ip)
    login_user_limiter.hit(ukey)


class Register(BaseModel):
    username: str = Field(max_length=32)
    password: str = Field(max_length=200)
    password2: str = Field(max_length=200)


class Login(BaseModel):
    username: str = Field(max_length=32)
    password: str = Field(max_length=200)


class PasswordOnly(BaseModel):
    password: str = Field(max_length=200)


class ChangePassword(BaseModel):
    old_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)
    new_password2: str = Field(max_length=200)


def _check_new_password(pw: str, pw2: str) -> None:
    if pw != pw2:
        raise HTTPException(400, "Salasanat eivät täsmää")
    if len(pw) < MIN_PASSWORD:
        raise HTTPException(400, f"Salasanan pitää olla vähintään {MIN_PASSWORD} merkkiä")


@router.post("/register", status_code=201)
def register(body: Register, request: Request, response: Response):
    ip = security.client_ip(request)
    register_limiter.check(ip)
    username = body.username.strip()
    if not USERNAME_RE.match(username):
        raise HTTPException(400, "Käyttäjätunnus: 3–32 merkkiä, kirjaimia, numeroita tai _ . -")
    _check_new_password(body.password, body.password2)

    pw_hash = security.hash_password(body.password)
    with db.connect() as con:
        if con.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise HTTPException(409, "Käyttäjätunnus on jo käytössä")
        cur = con.execute(
            "INSERT INTO users (username, pw_hash, created_at) VALUES (?, ?, ?)", (username, pw_hash, time.time())
        )
        user_id = cur.lastrowid
    register_limiter.hit(ip)
    security.create_session(response, user_id)
    return {"id": user_id, "username": username}


@router.post("/login")
def login(body: Login, request: Request, response: Response):
    ip = security.client_ip(request)
    ukey = "u:" + body.username.strip().lower()
    _login_check(ip, ukey)
    with db.connect() as con:
        row = con.execute("SELECT id, username, pw_hash FROM users WHERE username = ?", (body.username.strip(),)).fetchone()
    ok = security.verify_password(body.password, row["pw_hash"] if row else security.DUMMY_HASH)
    if not row or not ok:
        _login_fail(ip, ukey)
        raise HTTPException(401, "Väärä käyttäjätunnus tai salasana")
    security.create_session(response, row["id"])
    return {"id": row["id"], "username": row["username"]}


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response):
    security.destroy_session(request, response)


@router.get("/me")
def me(request: Request):
    user = security.current_user(request)
    if not user:
        return {"user": None, "orgs": []}
    with db.connect() as con:
        orgs = con.execute(
            "SELECT o.id, o.name, m.is_manager, (o.founder_id = ?) AS is_founder"
            " FROM memberships m JOIN orgs o ON o.id = m.org_id WHERE m.user_id = ? ORDER BY o.name",
            (user["id"], user["id"]),
        ).fetchall()
    return {
        "user": user,
        "orgs": [{"id": o["id"], "name": o["name"], "is_manager": bool(o["is_manager"]), "is_founder": bool(o["is_founder"])} for o in orgs],
    }


@router.post("/password", status_code=204)
def change_password(body: ChangePassword, request: Request, user: dict = Depends(security.require_user)):
    _login_check(security.client_ip(request), f"u:{user['username'].lower()}")
    _check_new_password(body.new_password, body.new_password2)
    with db.connect() as con:
        row = con.execute("SELECT pw_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
        if not security.verify_password(body.old_password, row["pw_hash"]):
            _login_fail(security.client_ip(request), f"u:{user['username'].lower()}")
            raise HTTPException(401, "Nykyinen salasana on väärä")
        con.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (security.hash_password(body.new_password), user["id"]))


@router.post("/delete", status_code=204)
def delete_account(body: PasswordOnly, request: Request, response: Response, user: dict = Depends(security.require_user)):
    with db.connect() as con:
        row = con.execute("SELECT pw_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
        if not security.verify_password(body.password, row["pw_hash"]):
            raise HTTPException(401, "Väärä salasana")
        if con.execute("SELECT 1 FROM orgs WHERE founder_id = ?", (user["id"],)).fetchone():
            raise HTTPException(409, "Poista ensin perustamasi organisaatiot")
        con.execute("DELETE FROM users WHERE id = ?", (user["id"],))  # CASCADE: sessiot, jäsenyydet, tilaukset
    response.delete_cookie(security.COOKIE, path="/")
