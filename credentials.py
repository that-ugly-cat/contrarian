"""Institutional TDM credentials — whose licence is this, exactly?

Elsevier and Wiley open their subscription full text to an *institutional*
licence, and a licence covers the people it names. It does not cover a server,
and it does not spread to everyone who can reach one. That makes publisher
credentials the wrong kind of thing to configure per deployment: an
`ELSEVIER_INSTTOKEN` in the environment would be usable by every API key that
reaches Contrarian, which is precisely the arrangement a library cannot sign
off on.

So subscription credentials belong to an API key, not to the server. Each key
carries its own Elsevier key + institutional token and Wiley TDM token, set by
the admin in /admin, and a run reaches subscription content only through the
credentials of the key that made the call. There is no separate "entitled"
switch: holding the credentials *is* the entitlement, which means the audit
question — whose licence retrieved this paper — always has one answer, the
holder of the calling key.

Springer stays in the environment on purpose. Its endpoint serves open-access
articles only, and nobody needs a licence to read those.

**At rest.** Elsevier's own guidance is that an institutional token
"represents full access to a customer account", so it is stored Fernet-
encrypted rather than in the clear: a copy of contrarian.db — a backup, a
scp'd file — carries no usable token. The secret that unlocks it (TDM_SECRET,
falling back to JWT_SECRET) lives in the environment, so this is protection
against a leaked database, not against a compromised host. The plaintext is
never rendered back into the admin page: a stored credential can be replaced
or cleared, never read out.

**In flight.** The credentials of the calling key are resolved once per
request, in the API-key middleware, and travel to the retrieval ladder in a
context variable — the MCP tool functions never see the request object. The
default is the empty set, so any path that bypasses the middleware, or any
future change that breaks the propagation, loses subscription access instead
of borrowing someone else's.
"""
import base64
import hashlib
import os
from contextvars import ContextVar
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken

FIELDS = ("elsevier_key", "elsevier_insttoken", "wiley_token")


@dataclass(frozen=True)
class TdmCredentials:
    """One key's subscription credentials, decrypted, for one request."""
    elsevier_key: str = ""
    elsevier_insttoken: str = ""
    wiley_token: str = ""

    def __bool__(self) -> bool:
        return bool(self.elsevier_key or self.wiley_token)


NONE = TdmCredentials()

_CALLER: ContextVar[TdmCredentials] = ContextVar("tdm_credentials", default=NONE)


# ── At rest ────────────────────────────────────────────────────────────────────

def _fernet() -> Fernet:
    secret = (os.environ.get("TDM_SECRET")
              or os.environ.get("JWT_SECRET")
              or os.environ.get("ADMIN_PASSWORD", "dev-secret"))
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.strip().encode()).decode() if value.strip() else ""


def decrypt(blob: str) -> str:
    """Plaintext, or "" if the blob is empty or no longer decryptable (a
    rotated secret must degrade to "no credentials", never to a crash)."""
    if not blob:
        return ""
    try:
        return _fernet().decrypt(blob.encode()).decode()
    except (InvalidToken, ValueError):
        return ""


# ── Reading and writing a key's credentials ────────────────────────────────────

def of_key(row) -> TdmCredentials:
    if row is None:
        return NONE
    return TdmCredentials(**{f: decrypt(getattr(row, f, "") or "") for f in FIELDS})


def store(row, values: dict, clear: bool = False) -> None:
    """Update a key's credentials in place. `values` maps field name to a new
    plaintext; a field absent or blank is left untouched, so the admin form can
    submit empty boxes without wiping what it never displayed. `clear` wipes
    all three — the only way to remove a credential, deliberately explicit."""
    for f in FIELDS:
        if clear:
            setattr(row, f, "")
        elif (values.get(f) or "").strip():
            setattr(row, f, encrypt(values[f]))


def summary(row) -> str:
    """What /admin shows about a key: which publishers are configured, never
    the credentials themselves."""
    creds = of_key(row)
    parts = []
    if creds.elsevier_key:
        parts.append("Elsevier" + ("" if creds.elsevier_insttoken else " (no insttoken)"))
    if creds.wiley_token:
        parts.append("Wiley")
    return " · ".join(parts) if parts else "open access only"


# ── In flight ──────────────────────────────────────────────────────────────────

def set_caller(row) -> None:
    """Pin the calling key's credentials for this request. Called once, in the
    middleware, before the app is entered: tasks spawned downstream inherit the
    value, and anything running outside this context keeps the empty default."""
    _CALLER.set(of_key(row))


def caller() -> TdmCredentials:
    """The credentials of whoever is making the current call."""
    return _CALLER.get()
