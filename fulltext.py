"""Full-text retrieval — DOI in, clean markdown out.

The provider ladder derived from LSSR's fulltext.py, made stateless: nothing is
written to disk. Contrarian fetches a text, converts it, hands it to the model
in context, and keeps only the *outcome* in the trace (provider used, status,
size). Publisher full texts are never archived server-side — the same line LSSR
drew when it refused Sci-Hub and PhilPapers redistribution: lawful private use
does not stretch to a server that stores and re-serves copyrighted text.

The ladder, in order of preference:

1. **Europe PMC fullTextXML** — cleanest source, no bot wall; JATS converted to
   markdown directly (references, figures, tables dropped).
2. **Direct PDF links** from Unpaywall and OpenAlex, repository copies first
   (publisher copies sit behind bot walls more often).
3. **Landing pages**, resolved via the citation_pdf_url meta tag most
   publishers emit (the same trick Zotero and Scholar use).
4. **Publisher TDM APIs** (Elsevier, Springer OA, Wiley) — the sanctioned way
   into subscription content, tried only when a key is configured and only for
   DOIs with that publisher's prefix.
5. **OA siblings** — OpenAlex indexes preprint and publisher versions as
   separate works, so when every rung fails on the requested DOI the ladder
   looks for a same-titled work under another DOI (an ACM paper's arXiv copy,
   a journal article's SocArXiv deposit) and climbs its OA locations too.

PDFs are converted through paper2md (references stripped — the model reads the
article, not its bibliography).

**Content verification.** OA location metadata is dirty: Unpaywall and
OpenAlex sometimes declare a repository deposit (Zenodo, most often) that is a
*different* paper as the OA copy of a DOI. So the ladder never trusts a
retrieval: every converted text is checked against the expected title of the
requested record (word-overlap against the document head), and a candidate
that does not match is discarded with a note — the ladder moves on. The
expected title comes from the caller (the run's own search records) or, as a
fallback, from Crossref; when no title can be found the result is flagged
title_verified: "unverified" rather than silently trusted.
"""
import os
import re
from urllib.parse import quote, urljoin

import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; Contrarian/1.0)"}
TIMEOUT = 20
MAX_CANDIDATES = 8


def _get(url: str, **kw):
    kw.setdefault("timeout", TIMEOUT)
    kw["headers"] = {**UA, **(kw.get("headers") or {})}
    return requests.get(url, **kw)


# ── Candidate locations ────────────────────────────────────────────────────────

def _europepmc(doi: str):
    results = []
    try:
        r = _get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                 params={"query": f'DOI:"{doi}"', "format": "json", "resultType": "core"})
        if r.status_code == 200:
            results = ((r.json().get("resultList") or {}).get("result") or [])[:1]
    except Exception:
        results = []
    for it in results:
        pmcid = it.get("pmcid")
        if pmcid and (it.get("isOpenAccess") == "Y" or it.get("inEPMC") == "Y"):
            yield ("xml", f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML")


def _unpaywall(doi: str, email: str):
    locs = []
    try:
        r = _get(f"https://api.unpaywall.org/v2/{doi}", params={"email": email})
        if r.status_code == 200:
            locs = r.json().get("oa_locations") or []
    except Exception:
        locs = []
    repo_first = sorted(locs, key=lambda l: 0 if l.get("host_type") == "repository" else 1)
    for loc in repo_first:
        if loc.get("url_for_pdf"):
            yield ("pdf", loc["url_for_pdf"])
    for loc in repo_first:
        if loc.get("url_for_landing_page"):
            yield ("landing", loc["url_for_landing_page"])


def _openalex(doi: str, email: str):
    data = {}
    try:
        r = _get(f"https://api.openalex.org/works/doi:{doi}", params={"mailto": email})
        if r.status_code == 200:
            data = r.json()
    except Exception:
        data = {}
    locs = [l for l in (data.get("locations") or []) if l.get("is_oa")]
    repo_first = sorted(locs, key=lambda l: 0 if (l.get("source") or {}).get("type") == "repository" else 1)
    for loc in repo_first:
        if loc.get("pdf_url"):
            yield ("pdf", loc["pdf_url"])
    best = data.get("best_oa_location") or {}
    if best.get("pdf_url"):
        yield ("pdf", best["pdf_url"])
    for loc in repo_first:
        if loc.get("landing_page_url"):
            yield ("landing", loc["landing_page_url"])
    if best.get("landing_page_url"):
        yield ("landing", best["landing_page_url"])


_KIND_ORDER = {"xml": 0, "pdf": 1, "landing": 2}


def candidates(doi: str, email: str) -> list[tuple[str, str]]:
    """Ordered, de-duplicated (kind, url): full-text XML first (cleanest, always
    reachable), then direct PDFs, then landing pages."""
    seen, out = set(), []
    for gen in (_europepmc(doi), _unpaywall(doi, email), _openalex(doi, email)):
        for kind, url in gen:
            if url and url not in seen:
                seen.add(url)
                out.append((kind, url))
    out.sort(key=lambda c: _KIND_ORDER[c[0]])
    return out[:MAX_CANDIDATES]


def sibling_candidates(doi: str, title: str, email: str) -> list[tuple[str, str, str]]:
    """(kind, url, sibling_doi) for OA copies of *other* OpenAlex works with the
    same title — usually the preprint sibling of a paywalled publisher record
    (an ACM paper's arXiv copy, a journal article's SocArXiv deposit). OpenAlex
    indexes preprint and publisher versions as separate works, so the main
    ladder never sees them; title verification downstream still guards against
    a same-titled different paper."""
    from difflib import SequenceMatcher
    tnorm = " ".join(_WORD.findall((title or "").lower()))
    if len(tnorm) < 12:
        return []
    try:
        r = _get("https://api.openalex.org/works",
                 params={"filter": f"title.search:{tnorm}", "per-page": 8,
                         "mailto": email})
        works = r.json().get("results", []) if r.status_code == 200 else []
    except Exception:
        works = []
    out = []
    for w in works:
        wdoi = (w.get("doi") or "").replace("https://doi.org/", "").lower()
        if not wdoi or wdoi == doi.lower():
            continue
        wnorm = " ".join(_WORD.findall((w.get("title") or "").lower()))
        if SequenceMatcher(None, tnorm, wnorm).ratio() < 0.9:
            continue
        best = w.get("best_oa_location") or {}
        if best.get("pdf_url"):
            out.append(("pdf", best["pdf_url"], wdoi))
        elif best.get("landing_page_url"):
            out.append(("landing", best["landing_page_url"], wdoi))
    return out[:4]


# ── JATS → markdown ────────────────────────────────────────────────────────────

_SKIP_TAGS = {"ref-list", "back", "fn-group", "table-wrap", "fig", "graphic",
              "supplementary-material", "table", "front", "journal-meta"}


def _itext(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _walk_jats(el, level: int, out: list):
    for child in el:
        tag = child.tag.split("}")[-1]
        if tag in _SKIP_TAGS:
            continue
        if tag == "sec":
            title = child.find("title")
            if title is not None:
                heading = _itext(title)
                if heading:
                    out.append("#" * min(level, 6) + " " + heading)
            _walk_jats(child, level + 1, out)
        elif tag == "title":
            continue
        elif tag in ("p", "caption"):
            text = _itext(child)
            if text:
                out.append(text)
        else:
            _walk_jats(child, level, out)


def _heading_level(block: str) -> int:
    return len(block) - len(block.lstrip("#"))


def _prune_empty_sections(blocks: list) -> list:
    kept = []
    for b in reversed(blocks):
        if b.startswith("#"):
            nxt = kept[-1] if kept else None
            if nxt is None or (nxt.startswith("#") and _heading_level(nxt) <= _heading_level(b)):
                continue
        kept.append(b)
    return list(reversed(kept))


def jats_to_markdown(xml_bytes: bytes) -> str:
    """JATS full text → markdown, the same shape paper2md returns for a PDF."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml_bytes)
    out = []
    title = root.find(".//article-title")
    if title is not None and _itext(title):
        out.append("# " + _itext(title))
    abstract = root.find(".//abstract")
    if abstract is not None:
        out.append("## Abstract")
        _walk_jats(abstract, 3, out)
    body = root.find(".//body")
    if body is not None:
        _walk_jats(body, 2, out)
    return "\n\n".join(_prune_empty_sections(out)).strip()


# ── Landing pages and PDFs ─────────────────────────────────────────────────────

_PDF_META = re.compile(
    r'<meta[^>]*?(?:name|property)=["\']citation_pdf_url["\'][^>]*?content=["\']([^"\']+)["\']', re.I)
_PDF_META_REV = re.compile(
    r'<meta[^>]*?content=["\']([^"\']+)["\'][^>]*?(?:name|property)=["\']citation_pdf_url["\']', re.I)


def pdf_from_landing(url: str) -> tuple[bytes | None, str | None]:
    """Resolve a landing page to a PDF: it may redirect straight to the PDF,
    otherwise we read citation_pdf_url out of the HTML."""
    try:
        r = _get(url, allow_redirects=True, timeout=25)
    except Exception:
        return None, None
    if not r.ok:
        return None, None
    ctype = r.headers.get("Content-Type", "")
    if "pdf" in ctype or r.content[:4] == b"%PDF":
        return (r.content if len(r.content) > 10_000 else None), r.url
    if "html" not in ctype:
        return None, None
    m = _PDF_META.search(r.text) or _PDF_META_REV.search(r.text)
    return None, urljoin(r.url, m.group(1)) if m else None


def _download_pdf(url: str) -> bytes | None:
    try:
        r = requests.get(url, timeout=30, allow_redirects=True, headers=UA)
        if r.status_code == 200 and len(r.content) > 10_000:
            if "pdf" in r.headers.get("Content-Type", "") or r.content[:4] == b"%PDF":
                return r.content
    except Exception:
        pass
    return None


def _fetch_jats(url: str) -> str | None:
    try:
        r = _get(url, timeout=30)
        if r.status_code != 200:
            return None
        head = r.content.lstrip()[:120]
        if not head.startswith((b"<?xml", b"<!DOCTYPE", b"<article")):
            return None
        md = jats_to_markdown(r.content)
        return md if len(md) > 500 else None
    except Exception:
        return None


# ── Publisher TDM APIs ─────────────────────────────────────────────────────────

ELSEVIER_PREFIXES = {"10.1016", "10.1006", "10.1053", "10.1054", "10.1078", "10.5555"}
WILEY_PREFIXES    = {"10.1002", "10.1111", "10.1046", "10.1034", "10.1049"}
SPRINGER_PREFIXES = {"10.1007", "10.1186", "10.1038", "10.1140", "10.1057", "10.1245"}


def _elsevier(doi: str, key: str, inst: str, notes: set) -> str | None:
    if not key:
        return None
    headers = {"X-ELS-APIKey": key}
    if inst:
        headers["X-ELS-Insttoken"] = inst
    url = f"https://api.elsevier.com/content/article/doi/{doi}"
    try:
        r = _get(url, headers={**headers, "Accept": "text/plain"}, timeout=45)
        if r.status_code == 200 and "text/plain" in r.headers.get("Content-Type", ""):
            text = r.text.strip()
            if len(text) > 500:
                return text
        if r.status_code in (401, 403):
            notes.add(f"Elsevier refused the key ({r.status_code})"
                      + ("" if inst else " — needs an institutional token"))
            return None
        r = _get(url, headers={**headers, "Accept": "application/json"}, timeout=45)
        if r.status_code == 200:
            body = (r.json().get("full-text-retrieval-response") or {})
            text = (body.get("originalText") or "")
            if isinstance(text, str) and len(text.strip()) > 500:
                return text.strip()
    except Exception:
        pass
    return None


def _springer(doi: str, key: str, notes: set) -> str | None:
    if not key:
        return None
    try:
        r = _get("https://api.springernature.com/openaccess/jats",
                 params={"q": f"doi:{doi}", "api_key": key}, timeout=45)
        if r.status_code in (401, 403):
            notes.add(f"Springer refused the key ({r.status_code})")
            return None
        if r.status_code == 200 and b"<" in r.content[:200]:
            md = jats_to_markdown(r.content)
            return md if len(md) > 500 else None
    except Exception:
        pass
    return None


def _wiley(doi: str, token: str, notes: set) -> bytes | None:
    if not token:
        return None
    try:
        r = _get(f"https://api.wiley.com/onlinelibrary/tdm/v1/articles/{quote(doi, safe='')}",
                 headers={"Wiley-TDM-Client-Token": token}, timeout=60, allow_redirects=True)
        if r.status_code in (401, 403):
            notes.add(f"Wiley refused the token ({r.status_code})")
            return None
        if r.status_code == 200 and r.content[:4] == b"%PDF" and len(r.content) > 10_000:
            return r.content
    except Exception:
        pass
    return None


def publisher_fulltext(doi: str, notes: set) -> tuple[str | None, bytes | None]:
    """(markdown, pdf_bytes) from whichever publisher owns this DOI prefix.
    Keys come from the environment; a publisher with no key is skipped, so an
    unconfigured Contrarian never calls out."""
    prefix = doi.split("/")[0]
    if prefix in ELSEVIER_PREFIXES:
        md = _elsevier(doi, os.environ.get("ELSEVIER_API_KEY", "").strip(),
                       os.environ.get("ELSEVIER_INSTTOKEN", "").strip(), notes)
        if md:
            return md, None
    if prefix in SPRINGER_PREFIXES:
        md = _springer(doi, os.environ.get("SPRINGER_API_KEY", "").strip(), notes)
        if md:
            return md, None
    if prefix in WILEY_PREFIXES:
        pdf = _wiley(doi, os.environ.get("WILEY_TDM_TOKEN", "").strip(), notes)
        if pdf:
            return None, pdf
    return None, None


# ── paper2md ───────────────────────────────────────────────────────────────────

def pdf_to_markdown(pdf_bytes: bytes) -> str:
    """POST the PDF to paper2md, references stripped (the model reads the
    article, not its bibliography). PAPER2MD_API_KEY lifts the upload cap."""
    paper2md_url = os.environ.get("PAPER2MD_URL", "http://localhost:8008")
    headers = {}
    key = os.environ.get("PAPER2MD_API_KEY", "").strip()
    if key:
        headers["X-API-Key"] = key
    resp = requests.post(
        f"{paper2md_url.rstrip('/')}/convert",
        files={"file": ("paper.pdf", pdf_bytes, "application/pdf")},
        data={"remove_references": "true", "remove_end_matter": "false", "format": "json"},
        headers=headers, timeout=360)
    if not resp.ok:
        code = resp.status_code
        body = (resp.text or "").strip()
        if code == 524 or (code >= 520 and "<html" in body[:400].lower()):
            raise RuntimeError(
                f"paper2md timed out at the proxy ({code}) — point PAPER2MD_URL at "
                "paper2md's internal address so the call skips the proxy.")
        raise RuntimeError(f"paper2md {code}: {body[:200]}")
    data = resp.json()
    return data.get("markdown") or data.get("text") or ""


# ── Content verification ───────────────────────────────────────────────────────

_WORD = re.compile(r"[a-z0-9]+")
LINE_THRESHOLD = 0.65   # sequence similarity: title vs a head line (or 2–3 joined)
BAG_THRESHOLD = 0.90    # fallback: near-total word coverage across the head


def _crossref_title(doi: str) -> str | None:
    try:
        r = _get(f"https://api.crossref.org/works/{quote(doi, safe='')}", timeout=10)
        if r.status_code == 200:
            titles = (r.json().get("message") or {}).get("title") or []
            return titles[0] if titles else None
    except Exception:
        pass
    return None


def title_matches(md: str, title: str) -> bool:
    """Does this text plausibly belong to a record with this title?

    Bag-of-words overlap is NOT enough here: within one literature the generic
    domain words (vitamin, common, cold…) appear in every paper, so a short
    title can 'match' a different paper entirely — the exact failure observed
    with Zenodo deposits. So the primary test demands the title as a
    contiguous thing: some line of the document head (or 2–3 adjacent lines,
    for wrapped titles) must contain the normalized title verbatim or resemble
    it by sequence similarity. Near-total word coverage (90%) remains as a
    fallback for heavily mangled front matter."""
    from difflib import SequenceMatcher
    tnorm = " ".join(_WORD.findall(title.lower()))
    if len(tnorm) < 12:
        return True                      # too short to verify meaningfully
    head = md[:3000].lower()
    lines = [" ".join(_WORD.findall(l)) for l in head.splitlines()]
    lines = [l for l in lines if l]
    for i in range(len(lines)):
        for j in (1, 2, 3):
            cand = " ".join(lines[i:i + j])
            if tnorm in cand:
                return True
            if SequenceMatcher(None, tnorm, cand).ratio() >= LINE_THRESHOLD:
                return True
    words = [w for w in tnorm.split() if len(w) > 3]
    if not words:
        return True
    headwords = " ".join(_WORD.findall(head))
    hits = sum(1 for w in words if w in headwords)
    return hits / len(words) >= BAG_THRESHOLD


# ── The ladder ─────────────────────────────────────────────────────────────────

def retrieve(doi: str, expected_title: str | None = None) -> dict:
    """Walk the ladder for one DOI. Returns:
      {"status": "ok"|"url_only"|"failed", "markdown": str, "provider": str,
       "url": str, "chars": int, "title_verified": True|"unverified",
       "notes": [str]}
    Every candidate that converts is checked against `expected_title` (falling
    back to Crossref when the caller has none): a text that does not match the
    requested record is discarded with a note and the ladder continues. On
    "url_only" the best OA link seen is returned so a human can fetch it
    manually; the markdown is empty."""
    doi = (doi or "").strip().replace("https://doi.org/", "")
    notes: set = set()
    if not doi:
        return {"status": "failed", "markdown": "", "provider": "", "url": "",
                "chars": 0, "title_verified": "unverified", "notes": ["no DOI given"]}
    email = os.environ.get("UNPAYWALL_EMAIL", "contrarian@borant.eu").strip()

    expected = (expected_title or "").strip() or _crossref_title(doi)
    if not expected:
        notes.add("no expected title available — retrieved content could not be verified")

    def _accept(md: str, provider: str, url: str) -> dict | None:
        if expected and not title_matches(md, expected):
            notes.add(f"discarded {provider} candidate — content does not match "
                      f"the requested record's title: {url}")
            return None
        return {"status": "ok", "markdown": md, "provider": provider, "url": url,
                "chars": len(md),
                "title_verified": True if expected else "unverified",
                "notes": sorted(notes)}

    fallback = None
    for kind, url in candidates(doi, email):
        if kind == "xml":
            md = _fetch_jats(url)
            if md and (r := _accept(md, "europepmc_xml", url)):
                return r
            continue
        pdf = None
        if kind == "pdf":
            fallback = fallback or url
            pdf = _download_pdf(url)
        else:
            pdf, pdf_url = pdf_from_landing(url)
            fallback = fallback or pdf_url or url
            if pdf is None and pdf_url:
                pdf = _download_pdf(pdf_url)
        if pdf:
            try:
                md = pdf_to_markdown(pdf)
            except RuntimeError as exc:
                notes.add(str(exc))
                continue
            if md and (r := _accept(md, "oa_pdf+paper2md", url)):
                return r

    md, pdf = publisher_fulltext(doi, notes)
    if md and (r := _accept(md, "publisher_tdm", f"https://doi.org/{doi}")):
        return r
    if pdf:
        try:
            md = pdf_to_markdown(pdf)
            if md and (r := _accept(md, "publisher_tdm+paper2md", f"https://doi.org/{doi}")):
                return r
        except RuntimeError as exc:
            notes.add(str(exc))

    # Last rung: OA siblings — same-titled OpenAlex works under another DOI
    # (preprint/publisher versions are separate works, invisible to the main
    # ladder). Only reachable with an expected title to match against.
    if expected:
        for kind, url, wdoi in sibling_candidates(doi, expected, email):
            pdf = None
            if kind == "pdf":
                pdf = _download_pdf(url)
            else:
                pdf, pdf_url = pdf_from_landing(url)
                if pdf is None and pdf_url:
                    pdf = _download_pdf(pdf_url)
            if not pdf:
                fallback = fallback or url
                continue
            try:
                md = pdf_to_markdown(pdf)
            except RuntimeError as exc:
                notes.add(str(exc))
                continue
            if md:
                notes.add(f"OA sibling record tried: {wdoi} "
                          f"(same title, different DOI)")
                if r := _accept(md, "oa_sibling+paper2md", url):
                    return r

    if fallback:
        return {"status": "url_only", "markdown": "", "provider": "",
                "url": fallback, "chars": 0, "title_verified": "unverified",
                "notes": sorted(notes)}
    return {"status": "failed", "markdown": "", "provider": "", "url": "",
            "chars": 0, "title_verified": "unverified", "notes": sorted(notes)}
