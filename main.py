"""Contrarian — claim verification against the literature, with the reasoning
kept visible.

FastAPI app wiring the three surfaces together:

- **/mcp** — the model-facing MCP server (streamable HTTP, X-API-Key), see
  mcp_app.py;
- **/api** — thin REST mirrors of search and fulltext for scripts and curl
  (same X-API-Key);
- **the web UI** — public catalog + piece pages (the glass box, see
  catalog.py), trace viewer and admin behind the admin login (see auth.py).

Port 8014. State: data/contrarian.db (API keys + traces only — no full texts).
"""
import contextlib
import json
import os

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import auth
import catalog
import fulltext as ft
import prompts
import sources
from mcp_app import mcp
from models import ApiKey, Run, SessionLocal, init_db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.globals["commit"] = catalog.commit_hash()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="Contrarian", lifespan=lifespan)

# The MCP transport validates Host headers against DNS rebinding — allow the
# public domain (from PUBLIC_URL) plus local access, or Caddy-proxied requests
# would all be rejected.
def _allowed_hosts() -> list[str]:
    from urllib.parse import urlparse
    hosts = ["localhost:8014", "127.0.0.1:8014", "localhost", "127.0.0.1"]
    public = urlparse(os.environ.get("PUBLIC_URL", "")).netloc
    if public:
        hosts.append(public)
    return hosts


from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402

app.mount("/mcp", mcp.streamable_http_app(
    streamable_http_path="/", json_response=True, stateless_http=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=_allowed_hosts(),
        allowed_origins=[os.environ.get("PUBLIC_URL", "http://localhost:8014")])))


# ── API-key gate for the model-facing surfaces ─────────────────────────────────

@app.middleware("http")
async def api_key_gate(request: Request, call_next):
    path = request.url.path
    if path.startswith("/mcp") or path.startswith("/api"):
        key = request.headers.get("X-API-Key", "")
        db = SessionLocal()
        try:
            ok = auth.check_api_key(db, key)
        finally:
            db.close()
        if not ok:
            return JSONResponse({"error": "missing or invalid X-API-Key"}, status_code=401)
    return await call_next(request)


# ── REST mirrors (stateless, for scripts) ──────────────────────────────────────

@app.post("/api/search")
async def api_search(request: Request):
    body = await request.json()
    try:
        return sources.search(body.get("database", "pubmed"), body.get("query", ""),
                              int(body.get("limit", 25)),
                              body.get("year_from"), body.get("year_to"))
    except sources.SearchError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.post("/api/fulltext")
async def api_fulltext(request: Request):
    body = await request.json()
    return ft.retrieve(body.get("doi", ""), expected_title=body.get("title"))


@app.get("/health")
def health():
    return {"status": "ok", "commit": catalog.commit_hash()}


# ── Public pages: the glass box ────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "pieces": catalog.build_catalog(),
        "is_admin": auth.is_admin(request)})


@app.get("/piece/{name}", response_class=HTMLResponse)
def piece(request: Request, name: str):
    p = catalog.get_piece(name)
    if p is None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "piece.html", {
        "p": p, "is_admin": auth.is_admin(request)})


# ── Login ──────────────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": ""})


@app.post("/login")
def login(request: Request, password: str = Form(...)):
    if not auth.check_admin_password(password):
        return templates.TemplateResponse(request, "login.html",
                                          {"error": "Wrong password."})
    resp = RedirectResponse("/runs", status_code=303)
    resp.set_cookie(auth.COOKIE, auth.make_session_cookie(),
                    httponly=True, samesite="lax",
                    max_age=auth.SESSION_HOURS * 3600)
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(auth.COOKIE)
    return resp


def _require_admin(request: Request):
    return None if auth.is_admin(request) else RedirectResponse("/login", status_code=303)


# ── Trace viewer (private: content, not method) ────────────────────────────────

@app.get("/runs", response_class=HTMLResponse)
def runs(request: Request):
    if (r := _require_admin(request)) is not None:
        return r
    db = SessionLocal()
    try:
        rows = db.query(Run).order_by(Run.created_at.desc()).limit(200).all()
        return templates.TemplateResponse(request, "runs.html",
                                          {"runs": rows, "is_admin": True})
    finally:
        db.close()


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(request: Request, run_id: str):
    if (r := _require_admin(request)) is not None:
        return r
    db = SessionLocal()
    try:
        run = db.query(Run).filter(Run.id == run_id).first()
        if run is None:
            return RedirectResponse("/runs", status_code=303)
        events = [{"seq": e.seq, "kind": e.kind, "at": e.created_at,
                   "data": e.data} for e in run.events]
        return templates.TemplateResponse(request, "run.html", {
            "run": run, "events": events,
            "versions": json.loads(run.protocol_versions or "{}"),
            "is_admin": True})
    finally:
        db.close()


# ── Admin: API keys ────────────────────────────────────────────────────────────

@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request):
    if (r := _require_admin(request)) is not None:
        return r
    db = SessionLocal()
    try:
        keys = db.query(ApiKey).order_by(ApiKey.created_at.desc()).all()
        return templates.TemplateResponse(request, "admin.html",
                                          {"keys": keys, "is_admin": True})
    finally:
        db.close()


@app.post("/admin/keys")
def create_key(request: Request, name: str = Form(...), notes: str = Form("")):
    if (r := _require_admin(request)) is not None:
        return r
    db = SessionLocal()
    try:
        db.add(ApiKey(name=name, notes=notes))
        db.commit()
    finally:
        db.close()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/keys/{key_id}/toggle")
def toggle_key(request: Request, key_id: int):
    if (r := _require_admin(request)) is not None:
        return r
    db = SessionLocal()
    try:
        row = db.query(ApiKey).filter(ApiKey.id == key_id).first()
        if row:
            row.active = not row.active
            db.commit()
    finally:
        db.close()
    return RedirectResponse("/admin", status_code=303)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8014")))
