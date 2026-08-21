"""Auth for the two surfaces.

Model-facing (/mcp, /api): X-API-Key header checked against the ApiKey table.
Keys are issued and revoked in /admin. The TDM/publisher credentials never
transit here — they live server-side in the environment.

Human-facing: the catalog, piece pages and prompts are public (they mirror a
public repo; hiding them would add friction, not security). The trace and the
admin panel sit behind a single admin password + JWT cookie — the
paper2md/pastebin pattern. Public what is method, private what is content.

Validating a key also answers a second question — which institutional TDM
credentials, if any, this caller brings — but those live on the key row and
are handled in credentials.py, not here.
"""
import ipaddress
import logging
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Request

log = logging.getLogger("contrarian.auth")

JWT_ALG = "HS256"
COOKIE = "contrarian_session"
SESSION_HOURS = 24 * 7

# Two ways of recognising the admin, and `local` is the default on purpose: an
# app that believes an identity header with nothing in front of it lets in
# anyone who sends that header. The gateway path stays dead code until someone
# turns it on deliberately.
#
#   local     the admin password in the environment, as it has always worked
#   gateway   an upstream SSO gate vouches for the caller via X-Borant-*
#
# Nothing about the key-facing surface changes in either mode: /mcp and /api
# keep their own X-API-Key, because a model client has no browser and no
# cookie — and because the TDM credentials that ride on a key are the whole
# point of knowing *which* key called.
AUTH_MODE = os.environ.get("AUTH_MODE", "local").strip().lower()

# In gateway mode identity headers are believed only from here — the reverse
# proxy, never the internet. Under Docker this is a bridge gateway and NOT
# 127.0.0.1; DEPLOY.md shows how to read the real value off a running container.
TRUSTED_PROXY = os.environ.get("BORANT_TRUSTED_PROXY", "127.0.0.1")


def _parse_trusted(raw: str) -> list:
    nets = []
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            nets.append(ipaddress.ip_network(chunk, strict=False))
        except ValueError:
            log.warning("BORANT_TRUSTED_PROXY: ignoring %r, not an address or CIDR", chunk)
    return nets


TRUSTED_PROXIES = _parse_trusted(TRUSTED_PROXY)


def gateway_mode() -> bool:
    return AUTH_MODE == "gateway"


def _from_trusted_proxy(request: Request) -> bool:
    peer = request.client.host if request.client else None
    if not peer:
        return False
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(addr in net for net in TRUSTED_PROXIES)


def _secret() -> str:
    return os.environ.get("JWT_SECRET") or os.environ.get("ADMIN_PASSWORD", "dev-secret")


def check_admin_password(password: str) -> bool:
    expected = os.environ.get("ADMIN_PASSWORD", "")
    return bool(expected) and password == expected


def make_session_cookie() -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)
    return jwt.encode({"admin": True, "exp": exp}, _secret(), algorithm=JWT_ALG)


def is_admin(request: Request) -> bool:
    if gateway_mode():
        # Reaching a gated route at all means the gate found a valid session and
        # a grant for this host, so there is nothing left for us to decide. The
        # local cookie is deliberately not consulted: a stale one must not
        # outlive a session revoked centrally.
        sub = request.headers.get("x-borant-sub")
        if not sub:
            return False
        if not _from_trusted_proxy(request):
            log.warning(
                "X-Borant-Sub from %s, outside BORANT_TRUSTED_PROXY (%s): ignored",
                request.client.host if request.client else "?", TRUSTED_PROXY)
            return False
        return True
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
    site, but the row itself carries the credentials the caller then pins."""
    from models import ApiKey, utcnow
    if not key:
        return None
    row = db.query(ApiKey).filter(ApiKey.key == key, ApiKey.active == True).first()  # noqa: E712
    if row is None:
        return None
    row.last_used_at = utcnow()
    db.commit()
    return row


    return row
