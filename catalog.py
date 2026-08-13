"""The glass box — everything the web UI shows about Contrarian's pieces.

The catalog never describes code, it *shows* it: a piece page renders
inspect.getsource() of the deployed object and the module docstring as the
recap, so what you read in the browser is what runs, verifiable against the
commit hash in the footer. Prompts are the same string the MCP server serves
via prompts/get — one object, two views. Nothing here is hand-maintained
documentation that can drift.

Three kinds of piece:
- tool     — an MCP tool the model calls (source = the tool function);
- prompt   — a protocol prompt (source = the prompt text, versioned);
- library  — a module the tools stand on (source = the whole module).
"""
import inspect
import os
import subprocess
from dataclasses import dataclass, field


@dataclass
class Piece:
    name: str
    kind: str          # tool | prompt | library
    summary: str       # one-liner for the card
    recap: str         # human-readable how/why (docstring or prompt title)
    source: str        # the code or the prompt text, verbatim
    version: str = ""  # prompts only
    meta: dict = field(default_factory=dict)


def _doc(obj) -> str:
    return inspect.getdoc(obj) or ""


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line.rstrip(".")
    return ""


def build_catalog() -> list[Piece]:
    import authors
    import fulltext
    import mcp_app
    import prompts
    import references
    import sources

    pieces: list[Piece] = []

    tool_fns = [mcp_app.start_run, mcp_app.search, mcp_app.get_fulltext,
                mcp_app.log_selection, mcp_app.log_verification, mcp_app.finish_run]
    for fn in tool_fns:
        pieces.append(Piece(
            name=fn.__name__, kind="tool",
            summary=_first_line(_doc(fn)),
            recap=_doc(fn),
            source=inspect.getsource(fn),
            meta={"signature": str(inspect.signature(fn))}))

    for p in prompts.ALL_PROMPTS:
        pieces.append(Piece(
            name=p["name"], kind="prompt",
            summary=p["title"],
            recap=f"{p['title']}. Served via MCP prompts/get; the version below "
                  "is stamped into every trace that runs under it.",
            source=p["text"], version=p["version"]))

    for mod in (sources, fulltext, references, authors):
        pieces.append(Piece(
            name=mod.__name__, kind="library",
            summary=_first_line(_doc(mod)),
            recap=_doc(mod),
            source=inspect.getsource(mod)))

    return pieces


def get_piece(name: str) -> Piece | None:
    return next((p for p in build_catalog() if p.name == name), None)


def commit_hash() -> str:
    """The deployed version: GIT_COMMIT env (set at Docker build) or a live
    git call in development. Shown in the footer of every page."""
    env = os.environ.get("GIT_COMMIT", "").strip()
    if env:
        return env[:12]
    try:
        return subprocess.run(["git", "rev-parse", "--short=12", "HEAD"],
                              capture_output=True, text=True, timeout=5,
                              cwd=os.path.dirname(os.path.abspath(__file__))
                              ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"
