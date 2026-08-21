"""Persistence — API keys and the run trace.

Two families of tables. ApiKey guards the model-facing surface (/mcp and
/api). Run + RunEvent are the trace: an append-only event log per verification
run, one event per pipeline step, each stamped with the protocol version it
ran under. The trace stores queries, hit counts, record *metadata*, selection
reasons, retrieval outcomes and quoted passages — never full texts (see
fulltext.py on why Contrarian doesn't archive papers).
"""
import json
import secrets
from datetime import datetime, timezone

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Integer,
                        String, Text, create_engine)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

DB_URL = "sqlite:///data/contrarian.db"
engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()


def utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    """Who a trace belongs to.

    Added late, and the reason it was missing is worth keeping: for a long time
    this service had exactly one human, so "the admin" and "the person whose
    trace this is" were the same row and neither needed a name. The moment a
    second key holder existed that stopped being true, and a list of runs with
    no owner column silently showed everyone everything.

    The app already insists on knowing *which key* retrieved a paper, because a
    publisher licence covers the people it names. This is the same question one
    level up — whose verification is this — and it deserves the same answer.

    Identity comes from the SSO gate (`borant_sub`) when there is one. A row can
    exist without it: a key issued to someone who has no account here yet still
    needs an owner, so that their runs are theirs from the first call.
    """
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    borant_sub = Column(String, unique=True, nullable=True, index=True)
    email = Column(String, nullable=True)
    name = Column(String, default="")
    # Key and TDM-credential management only. It does NOT open other people's
    # traces: those are content, and content stays with whoever produced it.
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)


class ApiKey(Base):
    __tablename__ = "api_keys"
    id = Column(Integer, primary_key=True)
    # Whose key this is. A run made with it belongs to this person.
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    name = Column(String, nullable=False)
    key = Column(String, unique=True, nullable=False,
                 default=lambda: "ctr_" + secrets.token_urlsafe(32))
    notes = Column(Text, default="")
    active = Column(Boolean, default=True)
    # Subscription full text (Elsevier, Wiley) is reachable only under an
    # institutional licence, and a licence covers *people*, not servers. So the
    # publisher credentials belong to the key rather than to the deployment:
    # holding them is the entitlement, and a run reaches subscription content
    # through the credentials of whoever called. Stored Fernet-encrypted and
    # never rendered back — see credentials.py.
    elsevier_key = Column(Text, default="")
    elsevier_insttoken = Column(Text, default="")
    wiley_token = Column(Text, default="")
    created_at = Column(DateTime, default=utcnow)
    last_used_at = Column(DateTime, nullable=True)


class Run(Base):
    __tablename__ = "runs"
    id = Column(String, primary_key=True,
                default=lambda: secrets.token_urlsafe(9))
    # Whose verification this is, taken from the key that made the call. Null
    # only for runs made before ownership existed; the backfill assigns those.
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    claim = Column(Text, nullable=False)
    status = Column(String, default="running")      # running | finished
    verdict = Column(String, default="")            # supported | contested | unsupported | no_evidence
    dossier = Column(Text, default="")              # resolved text, references appended
    protocol_versions = Column(Text, default="{}")  # {prompt: version} JSON
    # Traces are private by default (content, not method). A share token,
    # deliberately created by the admin, opens ONE run read-only at /r/{token}
    # — an unguessable capability URL, revocable by clearing the column.
    share_token = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    finished_at = Column(DateTime, nullable=True)
    events = relationship("RunEvent", back_populates="run",
                          order_by="RunEvent.seq", cascade="all, delete-orphan")


class RunEvent(Base):
    __tablename__ = "run_events"
    id = Column(Integer, primary_key=True)
    run_id = Column(String, ForeignKey("runs.id"), nullable=False)
    seq = Column(Integer, nullable=False)
    kind = Column(String, nullable=False)   # search | selection | fulltext | verification | finish
    payload = Column(Text, default="{}")    # JSON
    created_at = Column(DateTime, default=utcnow)
    run = relationship("Run", back_populates="events")

    @property
    def data(self) -> dict:
        try:
            return json.loads(self.payload)
        except Exception:
            return {}


def init_db():
    import os
    os.makedirs("data", exist_ok=True)
    Base.metadata.create_all(engine)
    # Column-level migrations for DBs created before share links and TDM
    # entitlement existed. Both are idempotent: added only when absent.
    with engine.connect() as conn:
        cols = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(runs)")]
        if "share_token" not in cols:
            conn.exec_driver_sql("ALTER TABLE runs ADD COLUMN share_token VARCHAR")
        # Ownership, added when a second key holder made "whose trace is this"
        # a real question. Existing rows land with NULL and are assigned by
        # backfill_owners.py — deliberately a script that prints what it did,
        # not a guess made at startup.
        if "user_id" not in cols:
            conn.exec_driver_sql("ALTER TABLE runs ADD COLUMN user_id INTEGER")
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_runs_user_id ON runs(user_id)")
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_runs_share_token "
            "ON runs(share_token)")
        keycols = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(api_keys)")]
        # Existing keys land with no credentials: institutional access is
        # granted deliberately, never inherited by a migration.
        for col in ("elsevier_key", "elsevier_insttoken", "wiley_token"):
            if col not in keycols:
                conn.exec_driver_sql(
                    f"ALTER TABLE api_keys ADD COLUMN {col} TEXT DEFAULT ''")
        if "user_id" not in keycols:
            conn.exec_driver_sql("ALTER TABLE api_keys ADD COLUMN user_id INTEGER")
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_api_keys_user_id ON api_keys(user_id)")
        if "tdm_entitled" in keycols:
            # Superseded: a boolean said who *may* use the server's shared
            # credentials, which left the credentials themselves belonging to
            # the deployment. Holding the credentials is now the entitlement.
            try:
                conn.exec_driver_sql("ALTER TABLE api_keys DROP COLUMN tdm_entitled")
            except Exception:
                pass    # SQLite < 3.35: harmless once the model stops mapping it
        conn.commit()


# ── Trace helpers ──────────────────────────────────────────────────────────────

def log_event(db, run: Run, kind: str, payload: dict) -> RunEvent:
    seq = len(run.events) + 1
    ev = RunEvent(run_id=run.id, seq=seq, kind=kind,
                  payload=json.dumps(payload, ensure_ascii=False))
    db.add(ev)
    db.commit()
    return ev


def get_run(db, run_id: str) -> Run | None:
    return db.query(Run).filter(Run.id == run_id).first()


def run_stats(run: Run) -> dict:
    """Procedural run statistics, computed from the trace — never from the
    model's memory of it. Appended to every dossier by finish_run, so the
    reader always knows how much of the verdict rests on full texts and how
    much on abstracts."""
    searches = [e.data for e in run.events if e.kind == "search"]
    fulltexts = [e.data for e in run.events if e.kind == "fulltext"]
    verifications = [e.data for e in run.events if e.kind == "verification"]
    selected = sum(len(e.data.get("selected", []))
                   for e in run.events if e.kind == "selection")
    ok_dois = {(f.get("doi") or "").strip().lower()
               for f in fulltexts if f.get("status") == "ok"}
    abstract_only = [v for v in verifications
                     if (v.get("key") or "").strip().lower() not in ok_dois]
    return {
        "searches": len(searches),
        "searches_pro": sum(1 for s in searches if s.get("stance") == "pro"),
        "searches_contra": sum(1 for s in searches if s.get("stance") == "contra"),
        "total_hits": sum(s.get("total") or 0 for s in searches),
        "records_returned": sum(s.get("returned") or 0 for s in searches),
        "records_unique": len(records_by_key(run)),
        "shortlisted": selected,
        "fulltext_attempted": len(fulltexts),
        "fulltext_ok": sum(1 for f in fulltexts if f.get("status") == "ok"),
        "fulltext_url_only": sum(1 for f in fulltexts if f.get("status") == "url_only"),
        "fulltext_failed": sum(1 for f in fulltexts if f.get("status") == "failed"),
        "papers_verified": len(verifications),
        "abstract_only": len(abstract_only),
    }


def records_by_key(run: Run) -> dict:
    """Every record this run has seen, keyed for [R:...] token resolution.
    Walking the search events (instead of a separate records table) keeps the
    trace as the single source of truth: a citable record IS a searched record."""
    from references import record_key
    out = {}
    for ev in run.events:
        if ev.kind != "search":
            continue
        for rec in ev.data.get("records", []):
            key = record_key(rec)
            if key and key not in out:
                out[key] = rec
    return out
