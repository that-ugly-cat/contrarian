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


class ApiKey(Base):
    __tablename__ = "api_keys"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    key = Column(String, unique=True, nullable=False,
                 default=lambda: "ctr_" + secrets.token_urlsafe(32))
    notes = Column(Text, default="")
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)
    last_used_at = Column(DateTime, nullable=True)


class Run(Base):
    __tablename__ = "runs"
    id = Column(String, primary_key=True,
                default=lambda: secrets.token_urlsafe(9))
    claim = Column(Text, nullable=False)
    status = Column(String, default="running")      # running | finished
    verdict = Column(String, default="")            # supported | contested | unsupported | no_evidence
    dossier = Column(Text, default="")              # resolved text, references appended
    protocol_versions = Column(Text, default="{}")  # {prompt: version} JSON
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
