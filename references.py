"""Procedural citation assembly — the hard guarantee against invented references.

The model never writes authors, years or DOIs in its synthesis. It cites with a
bare token — [R:10.1234/abc] (by DOI) or [R:url:...] (DOI-less records) — and
the token is replaced downstream with a citation string built *only* from the
metadata of a record that actually came back from a search in this run. A
reference to a paper the run never saw is structurally impossible: the token
simply fails to resolve, and unresolved tokens are reported, not hidden.

Same mechanism as LSSR's procedural citations in the synthesis step, extracted
to a standalone module. Citation format: APA-flavoured
  Surname, I., Surname, I., & Surname, I. (Year). Title. Source. https://doi.org/...
"""
import re

from authors import given_of, split_authors, surname_of

TOKEN = re.compile(r"\[R:([^\]\s]+)\]")
MAX_NAMED_AUTHORS = 6   # beyond this: first author + "et al."


def record_key(record: dict) -> str:
    """The token key a record answers to: its DOI, else its URL marked url:."""
    doi = (record.get("doi") or "").strip()
    if doi:
        return doi.lower()
    url = (record.get("url") or "").strip()
    return f"url:{url}" if url else ""


def _author_list(raw: str) -> str:
    names = split_authors(raw)
    if not names:
        return ""
    fmt = []
    for n in names[:MAX_NAMED_AUTHORS]:
        surname, given = surname_of(n), given_of(n)
        fmt.append(f"{surname}, {given[:1]}." if given else surname)
    if len(names) > MAX_NAMED_AUTHORS:
        return f"{fmt[0]}, et al."
    if len(fmt) > 1:
        return ", ".join(fmt[:-1]) + ", & " + fmt[-1]
    return fmt[0]


def format_citation(record: dict) -> str:
    """One record dict (the shape sources.py emits) → one citation string."""
    parts = []
    authors = _author_list(record.get("authors") or "")
    if authors:
        parts.append(authors)
    parts.append(f"({record['year']})." if record.get("year") else "(n.d.).")
    title = (record.get("title") or "").rstrip(".")
    if title:
        parts.append(title + ".")
    source = (record.get("source") or "").strip()
    if source:
        parts.append(source + ".")
    doi = (record.get("doi") or "").strip()
    if doi:
        parts.append(f"https://doi.org/{doi}")
    elif record.get("url"):
        parts.append(record["url"])
    return " ".join(parts)


def resolve(text: str, records_by_key: dict) -> tuple[str, list[str], list[str]]:
    """Replace every [R:key] token in `text` with (Surname et al., Year) inline
    markers and build the numbered reference list. Returns
    (resolved_text, references, unresolved_keys)."""
    order: list[str] = []
    unresolved: list[str] = []

    def _sub(m):
        key = m.group(1).lower()
        rec = records_by_key.get(key)
        if rec is None:
            unresolved.append(m.group(1))
            return m.group(0)          # leave the token visible — never fake it
        if key not in order:
            order.append(key)
        names = split_authors(rec.get("authors") or "")
        first = surname_of(names[0]) if names else "Anon."
        label = first if len(names) == 1 else f"{first} et al."
        year = rec.get("year") or "n.d."
        return f"({label}, {year})"

    resolved = TOKEN.sub(_sub, text)
    references = [format_citation(records_by_key[k]) for k in order]
    return resolved, references, sorted(set(unresolved))
