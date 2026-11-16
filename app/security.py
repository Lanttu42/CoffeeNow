"""Salasanat, sessiot ja rate limit. Vain Pythonin vakiokirjasto (hashlib.scrypt)."""
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, Response

from . import db

COOKIE = "cn_session"
SESSION_TTL = 30 * 24 * 3600
# Paikallisessa kehityksessä (http://localhost) voi asettaa COOKIE_SECURE=0
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "1") != "0"

# scrypt: ~16 MB muistia per hash -> hidas brute forcelle, nopea ihmiselle
_N, _R, _P = 2**14, 8, 1


# ---------- salasanat ----------

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        expected = bytes.fromhex(digest)
        calc = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(calc, expected)
    except (ValueError, TypeError):
        return False


# Tuntemattoman käyttäjän kirjautuminen tekee saman työn -> vastausaika ei paljasta tunnuksia
DUMMY_HASH = hash_password(secrets.token_hex(16))


# ---------- sessiot ----------

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(response: Response, user_id: int) -> None:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with db.connect() as con:
        con.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        con.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (_token_hash(token), user_id, now, now + SESSION_TTL),
        )
    response.set_cookie(
        COOKIE, token, max_age=SESSION_TTL, httponly=True, secure=COOKIE_SECURE, samesite="lax", path="/"
    )


def destroy_session(request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE)
    if token:
        with db.connect() as con:
            con.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
    response.delete_cookie(COOKIE, path="/")


def current_user(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE)
    if not token or len(token) > 100:
        return None
    with db.connect() as con:
        row = con.execute(
            "SELECT u.id, u.username FROM sessions s JOIN users u ON u.id = s.user_id"
            " WHERE s.token_hash = ? AND s.expires_at > ?",
            (_token_hash(token), time.time()),
        ).fetchone()
    return dict(row) if row else None


def require_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(401, "login_required")
    return user


# ---------- rate limit ----------

class Limiter:
    """Liukuva ikkuna: korkeintaan max_hits osumaa per avain per window sekuntia."""

    def __init__(self, max_hits: int, window: float, message: str = "Liikaa yrityksiä, odota hetki ☕"):
        self.max_hits, self.window, self.message = max_hits, window, message
        self.hits: dict[str, deque] = defaultdict(deque)

    def check(self, *keys: str) -> None:
        now = time.time()
        for key in keys:
            q = self.hits[key]
            while q and q[0] < now - self.window:
                q.popleft()
            if len(q) >= self.max_hits:
                raise HTTPException(429, self.message)

    def hit(self, *keys: str) -> None:
        if len(self.hits) > 50_000:  # muistin suojaus
            self.hits.clear()
        for key in keys:
            self.hits[key].append(time.time())


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


# ---------- kutsukoodit ----------

# Ei sekoittuvia merkkejä (0/O, 1/I/L)
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def new_invite_code() -> str:
    s = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
    return f"{s[:3]}-{s[3:]}"


def normalize_invite_code(code: str) -> str:
    c = "".join(ch for ch in code.upper() if ch.isalnum())
    return f"{c[:3]}-{c[3:]}" if len(c) == 6 else ""
