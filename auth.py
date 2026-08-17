"""Auth for the two surfaces.

Model-facing (/mcp, /api): X-API-Key header checked against the ApiKey table.
Keys are issued and revoked in /admin. The TDM/publisher credentials never
transit here — they live server-side in the environment.

Human-facing: the catalog, piece pages and prompts are public (they mirror a
public repo; hiding them would add friction, not security). The trace and the
admin panel sit behind a single admin password + JWT cookie — the
paper2md/pastebin pattern. Public what is method, private what is content.

**Entitlement.** Validating a key also answers a second question: may *this*
caller reach subscription full text? An institutional TDM licence covers the
people it names, not the machine their software runs on, so the publisher
credentials configured on the server are usable only by keys explicitly
marked tdm_entitled — issuing a key does not hand out institutional access
with it. The answer travels from the middleware to the retrieval ladder in a
context variable (the MCP tool functions never see the request), and it
defaults to *not entitled*: if that propagation ever broke, the ladder would
lose subscription access, never leak it.
"""
import os
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Request

_TDM_ENTITLED: ContextVar[bool] = ContextVar("tdm_entitled", default=False)

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


def check_api_key(db, key: str):
    """The active ApiKey row for this key, or None. Truthy/falsy at the call
    site, but the row itself carries the entitlement the caller then pins."""
    from models import ApiKey, utcnow
    if not key:
        return None
    row = db.query(ApiKey).filter(ApiKey.key == key, ApiKey.active == True).first()  # noqa: E712
    if row is None:
        return None
    row.last_used_at = utcnow()
    db.commit()
    return row


def set_caller_entitlement(row) -> None:
    """Pin this request's TDM entitlement for everything downstream. Called
    once per request, right after the key check, before the app is entered:
    tasks spawned from here inherit the value, and anything that runs outside
    this context keeps the safe default (False)."""
    _TDM_ENTITLED.set(bool(row is not None and row.tdm_entitled))


def caller_is_tdm_entitled() -> bool:
    """May the current caller use the server's publisher TDM credentials?"""
    return _TDM_ENTITLED.get()
