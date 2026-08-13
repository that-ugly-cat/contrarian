"""The MCP surface — what a model in conversation can actually do.

Six tools and five prompts, built with the official MCP Python SDK (FastMCP),
served over streamable HTTP at /mcp behind the X-API-Key check.

The tools are deliberately narrow. The server searches, retrieves and logs;
every judgment (query wording, shortlist, reading, verdict) stays with the
model, in conversation, where the human can see and interrupt it. The two
log_* tools exist precisely because judgment steps leave no server-side trace
of their own — the protocol makes the model deposit its reasons, so the trace
viewer can show why the shortlist and the verdict are what they are.

The prompts are the protocol (see prompts.py): a client fetches verify_claim
via prompts/get and the model follows it, logging as it goes.

MAX_FULLTEXT_CHARS caps what get_fulltext returns into context; the cut is
declared in the payload (truncated: true), never silent.
"""
import json

from mcp.server.mcpserver import MCPServer

import fulltext as ft
import prompts
import references
import sources
from models import (Run, SessionLocal, get_run, log_event, records_by_key,
                    utcnow)

MAX_FULLTEXT_CHARS = 150_000

mcp = MCPServer(
    "contrarian",
    instructions="Claim verification against the scientific literature. "
                 "Fetch the verify_claim prompt for the full protocol; "
                 "start_run() opens a trace, every judgment gets logged.")


def _fail(msg: str) -> dict:
    return {"error": msg}


# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def start_run(claim: str) -> dict:
    """Open a verification run for a claim. Returns run_id (pass it to every
    later call) and the protocol versions this run is stamped with. One run =
    one claim = one trace page."""
    claim = (claim or "").strip()
    if not claim:
        return _fail("empty claim")
    db = SessionLocal()
    try:
        run = Run(claim=claim,
                  protocol_versions=json.dumps(prompts.versions()))
        db.add(run)
        db.commit()
        return {"run_id": run.id, "protocol_versions": prompts.versions(),
                "next": "formulate pro + steelman-contra queries, then search()"}
    finally:
        db.close()


@mcp.tool()
def search(run_id: str, database: str, query: str, stance: str,
           limit: int = 25, year_from: int | None = None,
           year_to: int | None = None) -> dict:
    """Run one literature search and log it to the trace.

    database: pubmed | europepmc | openalex (native query syntax each).
    stance: pro | contra — which side of the claim this query serves.
    Returns the database's total hit count plus up to `limit` relevance-ranked
    records, each with a `key` usable in [R:key] citation tokens. On a syntax
    error the database's own complaint comes back — fix the query and retry."""
    db = SessionLocal()
    try:
        run = get_run(db, run_id)
        if run is None:
            return _fail(f"unknown run_id {run_id}")
        if stance not in ("pro", "contra"):
            return _fail("stance must be 'pro' or 'contra'")
        try:
            result = sources.search(database, query, limit, year_from, year_to)
        except sources.SearchError as exc:
            log_event(db, run, "search",
                      {"database": database, "query": query, "stance": stance,
                       "error": str(exc)})
            return _fail(str(exc))
        for rec in result["records"]:
            rec["key"] = references.record_key(rec)
        log_event(db, run, "search",
                  {"database": database, "query": query, "stance": stance,
                   "total": result["total"], "returned": len(result["records"]),
                   "records": result["records"]})
        return {"total": result["total"], "records": result["records"]}
    finally:
        db.close()


@mcp.tool()
def get_fulltext(run_id: str, doi: str) -> dict:
    """Retrieve one paper's full text as markdown, walking the OA ladder
    (Europe PMC XML → OA PDFs via paper2md → landing pages → publisher TDM).
    Every retrieved text is verified against the record's title before being
    trusted — OA metadata sometimes points a DOI at a different paper's
    repository deposit, and a mismatched candidate is discarded with a note.
    Logs the outcome (never the text) to the trace. status: ok | url_only
    (only a link found — a human can fetch it manually) | failed."""
    db = SessionLocal()
    try:
        run = get_run(db, run_id)
        if run is None:
            return _fail(f"unknown run_id {run_id}")
        rec = records_by_key(run).get((doi or "").strip().lower())
        result = ft.retrieve(doi, expected_title=(rec or {}).get("title"))
        log_event(db, run, "fulltext",
                  {"doi": doi, "status": result["status"],
                   "provider": result["provider"], "url": result["url"],
                   "chars": result["chars"],
                   "title_verified": result["title_verified"],
                   "notes": result["notes"]})
        md = result.pop("markdown")
        if len(md) > MAX_FULLTEXT_CHARS:
            result["truncated"] = True
            md = md[:MAX_FULLTEXT_CHARS]
        result["markdown"] = md
        return result
    finally:
        db.close()


@mcp.tool()
def log_selection(run_id: str, stance: str, selected: list[dict],
                  excluded: list[dict] | None = None) -> dict:
    """Log the shortlist decision for one stance. selected/excluded entries:
    {"key": record key from search results, "reason": one sentence}. The trace
    must show why the shortlist is what it is — including what was left out."""
    db = SessionLocal()
    try:
        run = get_run(db, run_id)
        if run is None:
            return _fail(f"unknown run_id {run_id}")
        known = records_by_key(run)
        unknown = [e.get("key") for e in (selected + (excluded or []))
                   if e.get("key", "").lower() not in known]
        log_event(db, run, "selection",
                  {"stance": stance, "selected": selected,
                   "excluded": excluded or [], "unknown_keys": unknown})
        if unknown:
            return {"ok": True, "warning": f"keys never seen in this run's searches: {unknown}"}
        return {"ok": True}
    finally:
        db.close()


@mcp.tool()
def log_verification(run_id: str, key: str, paper_verdict: str,
                     passages: list[dict]) -> dict:
    """Log the reading of one paper. paper_verdict: supports | contradicts |
    mixed | irrelevant. passages entries: {"quote": verbatim text, "location":
    section, "bearing": supports_directly | supports_indirectly | contradicts |
    qualifies, "why": one sentence}. Quotes are the evidence — a paper with no
    quotable passage bearing on the claim is 'irrelevant', not weak support."""
    db = SessionLocal()
    try:
        run = get_run(db, run_id)
        if run is None:
            return _fail(f"unknown run_id {run_id}")
        known = records_by_key(run)
        payload = {"key": key, "paper_verdict": paper_verdict, "passages": passages}
        if key.lower() not in known:
            payload["unknown_key"] = True
        log_event(db, run, "verification", payload)
        return {"ok": True}
    finally:
        db.close()


@mcp.tool()
def finish_run(run_id: str, verdict: str, dossier: str) -> dict:
    """Close the run. verdict: supported | contested | unsupported |
    no_evidence. The dossier cites only with [R:key] tokens; the server
    resolves them into real references from the run's own search records and
    returns the final text + reference list + trace URL. Unresolved tokens
    come back as failures — report them as such, never as citations."""
    import os
    db = SessionLocal()
    try:
        run = get_run(db, run_id)
        if run is None:
            return _fail(f"unknown run_id {run_id}")
        if verdict not in ("supported", "contested", "unsupported", "no_evidence"):
            return _fail("verdict must be supported | contested | unsupported | no_evidence")
        resolved, refs, unresolved = references.resolve(dossier, records_by_key(run))
        if refs:
            resolved += "\n\nReferences\n" + "\n".join(
                f"{i + 1}. {r}" for i, r in enumerate(refs))
        run.verdict = verdict
        run.dossier = resolved
        run.status = "finished"
        run.finished_at = utcnow()
        db.commit()
        log_event(db, run, "finish",
                  {"verdict": verdict, "references": len(refs),
                   "unresolved_tokens": unresolved})
        base = os.environ.get("PUBLIC_URL", "http://localhost:8014").rstrip("/")
        return {"dossier": resolved, "references": refs,
                "unresolved_tokens": unresolved,
                "trace_url": f"{base}/runs/{run.id}"}
    finally:
        db.close()


# ── Prompts (the protocol, served via prompts/get) ────────────────────────────

def _register_prompts():
    for p in prompts.ALL_PROMPTS:
        def make(prompt):
            def fn(claim: str = "", stance: str = "", max_select: str = "5") -> str:
                text = prompt["text"]
                for k, v in (("{claim}", claim), ("{stance}", stance),
                             ("{max_select}", max_select)):
                    text = text.replace(k, v or k)
                return text
            fn.__name__ = prompt["name"]
            fn.__doc__ = f"{prompt['title']} (v{prompt['version']})"
            return fn
        mcp.prompt(name=p["name"], description=f"{p['title']} (v{p['version']})")(make(p))


_register_prompts()
