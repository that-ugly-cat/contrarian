"""Auth for the two surfaces.

Model-facing (/mcp, /api): X-API-Key header checked against the ApiKey table.
Keys are issued and revoked in /admin. The TDM/publisher credentials never
transit here — they live server-side in the environment.

Human-facing: the catalog, piece pages and prompts are public (they mirror a
public repo; hiding them would add friction, not security). The trace and the
admin panel sit behind a single admin password + JWT cookie — the
paper2md/pastebin pattern. Public what is method, private what is content.
"""
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Request

JWT_ALG = "HS256"
COOKIE = "contrarian_session"
SESSION_HOURS = 24 * 7


def _secret() -> str:
    return os.environ.get("JWT_SECRET") or os.environ.get("ADMIN_PASSWORD", "dev-secret")


def check_admin_password(password: str) -> bool:
    expected = os.environ.get("ADMIN_PASSWORD", "")
    return bool(expected) and password == expected


def make_session_cookie() -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)
    return jwt.encode({"admin": True, "exp": exp}, _secret(), algorithm=JWT_ALG)


def is_admin(request: Request) -> bool:
    token = request.cookies.get(COOKIE, "")
    if not token:
        return False
    try:
        data = jwt.decode(token, _secret(), algorithms=[JWT_ALG])
        return bool(data.get("admin"))
    except jwt.PyJWTError:
        return False


def check_api_key(db, key: str) -> bool:
    from models import ApiKey, utcnow
    if not key:
        return False
    row = db.query(ApiKey).filter(ApiKey.key == key, ApiKey.active == True).first()  # noqa: E712
    if row is None:
        return False
    row.last_used_at = utcnow()
    db.commit()
    return True
