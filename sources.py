"""Search adapters — query in, capped record list out.

Three literature databases with free APIs, derived from LSSR's harvest modules
(pubmed.py, europepmc.py, openalex.py) but reshaped for per-claim use:

- **synchronous and capped** — a claim check needs the top-N relevant records,
  not an exhaustive corpus, so there are no background jobs: each adapter asks
  the API for relevance-ranked results and stops at `limit`;
- **total count always returned** — the model iterates on the query using the
  hit count as feedback (0 hits → too narrow, 50k hits → too broad), the same
  way a human refines a PubMed search;
- **normalized record shape** — every adapter emits the same dict (title,
  abstract, authors canonical "; "-separated, year, doi, url, source, database)
  so downstream steps never care where a record came from.

Query syntax is the native syntax of each database. PubMed is the richest
(MeSH + field tags) and the default. Europe PMC caveat inherited from LSSR:
its MESH: field silently collapses on multi-word headings — use KW: instead,
which tracks [Mesh:noexp] closely.
"""
import json
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus, urlencode

import urllib3

from authors import canonicalize, join_authors

http = urllib3.PoolManager()

DATABASES = ("pubmed", "europepmc", "openalex")
DEFAULT_LIMIT = 25
MAX_LIMIT = 100

NCBI_RATE = 0.4          # seconds between E-utilities requests (NCBI policy)
PUBMED_FETCH_BATCH = 50  # PMIDs per efetch call


class SearchError(RuntimeError):
    """The database rejected the query — the message is meant to go back to the
    model verbatim so it can fix the syntax and retry."""


def _norm(record: dict) -> dict:
    record.setdefault("abstract", "")
    record.setdefault("doi", "")
    record.setdefault("url", "")
    record.setdefault("source", "")
    return record


# ── PubMed ─────────────────────────────────────────────────────────────────────

def _parse_medline(article: str) -> dict:
    """One MEDLINE record → normalized dict (regex parse from LSSR/TopicTracker)."""
    article = re.sub(re.compile(r"\n\s{2,}", re.MULTILINE), " ", article)

    def find(pattern):
        m = re.search(pattern, article)
        return m.group(0).strip() if m else ""

    def findall(pattern):
        return [x.strip() for x in re.findall(re.compile(pattern), article)]

    year = find(r"(?<=DP\s\s-\s)\d{4}")
    doi = find(r"(?<=AID\s-\s).*(?=\s\[doi)") or find(r"(?<=LID\s-\s).*(?=\s\[doi)")
    pmid = find(r"(?<=PMID-\s)\d+")
    # DOI-less records (mostly pre-2000) still need a citable identity — fall
    # back to the PubMed URL so their [R:url:...] key exists.
    url = (f"https://doi.org/{doi}" if doi
           else f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "")
    return _norm({
        "title": find(r"(?<=TI\s\s-\s).*") or find(r"(?<=BTI\s-\s).*"),
        "abstract": find(r"(?<=AB\s\s-\s).*") or find(r"(?<=OAB\s-\s).*"),
        "authors": (join_authors(findall(r"(?<=FAU\s-\s).*"))
                    or join_authors(findall(r"(?<=AU\s\s-\s).*"))),
        "year": int(year) if year else None,
        "doi": doi,
        "url": url,
        "source": find(r"(?<=JT\s\s-\s).*") or find(r"(?<=PB\s\s-\s).*"),
        "database": "pubmed",
    })


def search_pubmed(query: str, limit: int,
                  year_from: int | None, year_to: int | None) -> tuple[int, list[dict]]:
    if year_from and year_to:
        query = f"({query}) AND ({year_from}:{year_to}[pdat])"
    url = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
           f"?db=pubmed&retmax={limit}&sort=relevance&term={quote_plus(query)}")
    resp = http.request("GET", url)
    if resp.status >= 400:
        raise SearchError(f"PubMed rejected the query (HTTP {resp.status})")
    root = ET.fromstring(resp.data.decode("utf-8"))
    for err in root.findall(".//ErrorList/"):
        raise SearchError(f"PubMed: {err.tag} — {err.text}")
    total = int(root.findtext("Count") or 0)
    pmids = [x.text for x in root.findall("IdList/Id")]

    records = []
    for i in range(0, len(pmids), PUBMED_FETCH_BATCH):
        time.sleep(NCBI_RATE)
        batch = ",".join(pmids[i:i + PUBMED_FETCH_BATCH])
        furl = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
                f"?db=pubmed&rettype=medline&id={batch}")
        text = http.request("GET", furl).data.decode("utf-8")
        for chunk in re.split(r"\n(?=PMID- )", text):
            if chunk.strip():
                rec = _parse_medline(chunk)
                if rec["title"]:
                    records.append(rec)
    return total, records


# ── Europe PMC ─────────────────────────────────────────────────────────────────

def search_europepmc(query: str, limit: int,
                     year_from: int | None, year_to: int | None) -> tuple[int, list[dict]]:
    q = query
    if year_from and year_to:
        q = f"({query}) AND (PUB_YEAR:[{year_from} TO {year_to}])"
    params = {"query": q, "format": "json", "pageSize": limit,
              "cursorMark": "*", "resultType": "core"}   # default sort: relevance
    resp = http.request(
        "GET", f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?{urlencode(params)}")
    if resp.status >= 400:
        raise SearchError(f"Europe PMC rejected the query (HTTP {resp.status}): "
                          f"{resp.data.decode('utf-8', 'replace')[:200]}")
    data = json.loads(resp.data.decode("utf-8"))
    total = data.get("hitCount", 0)
    records = []
    for r in (data.get("resultList") or {}).get("result", []):
        doi = r.get("doi", "") or ""
        src, pmid = r.get("source", ""), r.get("pmid") or r.get("id") or ""
        journal = ((r.get("journalInfo") or {}).get("journal") or {}).get("title", "")
        year = r.get("pubYear")
        records.append(_norm({
            "title": r.get("title", "") or "",
            "abstract": r.get("abstractText", "") or "",
            "authors": (join_authors(a.get("fullName", "") for a in
                                     ((r.get("authorList") or {}).get("author") or []))
                        or canonicalize(r.get("authorString", ""))),
            "year": int(year) if year and str(year).isdigit() else None,
            "doi": doi,
            "url": (f"https://doi.org/{doi}" if doi
                    else f"https://europepmc.org/article/{src}/{pmid}" if src and pmid else ""),
            "source": journal or src or "",
            "database": "europepmc",
        }))
    return total, records


# ── OpenAlex ───────────────────────────────────────────────────────────────────

def _reconstruct_abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    positions = [(i, word) for word, idxs in inv.items() for i in idxs]
    positions.sort()
    return " ".join(w for _, w in positions)


def search_openalex(query: str, limit: int,
                    year_from: int | None, year_to: int | None) -> tuple[int, list[dict]]:
    import os
    filt = f"title_and_abstract.search:{query}"
    if year_from and year_to:
        filt += (f",from_publication_date:{year_from}-01-01"
                 f",to_publication_date:{year_to}-12-31")
    params = {"filter": filt, "per-page": limit, "sort": "relevance_score:desc"}
    mailto = os.environ.get("OPENALEX_MAILTO", "").strip()
    if mailto:
        params["mailto"] = mailto
    resp = http.request("GET", f"https://api.openalex.org/works?{urlencode(params)}")
    data = json.loads(resp.data.decode("utf-8"))
    if resp.status >= 400 or "error" in data:
        msg = data.get("message") or data.get("error") or f"HTTP {resp.status}"
        raise SearchError(f"OpenAlex rejected the query: {msg}")
    total = data.get("meta", {}).get("count", 0)
    records = []
    for r in data.get("results", []):
        doi = (r.get("doi") or "").replace("https://doi.org/", "")
        year = r.get("publication_year")
        records.append(_norm({
            "title": r.get("title") or r.get("display_name") or "",
            "abstract": _reconstruct_abstract(r.get("abstract_inverted_index")),
            "authors": join_authors(
                (a.get("author") or {}).get("display_name", "")
                for a in r.get("authorships", []) if a.get("author")),
            "year": int(year) if year else None,
            "doi": doi,
            "url": f"https://doi.org/{doi}" if doi else (r.get("id", "") or ""),
            "source": ((r.get("primary_location") or {}).get("source") or {})
                      .get("display_name", "") or "",
            "database": "openalex",
        }))
    return total, records


# ── Dispatch ───────────────────────────────────────────────────────────────────

_ADAPTERS = {"pubmed": search_pubmed, "europepmc": search_europepmc,
             "openalex": search_openalex}


def search(database: str, query: str, limit: int = DEFAULT_LIMIT,
           year_from: int | None = None, year_to: int | None = None) -> dict:
    """Run one capped search. Returns {"total": int, "records": [dict]}.
    `total` is the database's full hit count; `records` holds at most `limit`
    relevance-ranked entries. Raises SearchError with the database's own
    complaint on bad syntax, so the caller can fix the query and retry."""
    if database not in _ADAPTERS:
        raise SearchError(f"unknown database '{database}' — use one of {DATABASES}")
    limit = max(1, min(int(limit), MAX_LIMIT))
    total, records = _ADAPTERS[database](query.strip(), limit, year_from, year_to)
    return {"total": total, "records": records[:limit]}
