"""The verification protocol — every instruction Contrarian gives a model.

One file to read for the full audit of what the model is asked to do, the same
choice LSSR made with its prompts.py. Each prompt carries its own semantic
version; every trace event logs the version it ran under, so a dossier is
reproducible: claim + protocol version + records seen → verdict. Change a
prompt, bump its version — git history becomes the history of the protocol.

The four step prompts map to the model-side steps of the pipeline (the
tool-side steps — search, retrieval, citation assembly — live in code, not
prompts). VERIFY_CLAIM is the master protocol binding them: it is what a
client fetches via MCP `prompts/get` to run a full verification.

Design commitments the prompts encode:
- the *contra* pass is a steelman, not a syntactic negation;
- evidence means verbatim passages with their location, never paraphrase alone;
- absence of evidence is an outcome, distinct from evidence of absence;
- references are procedural tokens ([R:doi]) — the model never writes
  author/year/DOI by hand.
"""

PROTOCOL_VERSION = "1.0.0"


def _p(name: str, version: str, title: str, text: str) -> dict:
    return {"name": name, "version": version, "title": title, "text": text.strip()}


FORMULATE_QUERIES = _p(
    "formulate_queries", "1.1.0", "Claim → search queries (pro + steelman contra)",
    """
You are formulating literature search queries to verify a claim.

CLAIM: {claim}

Produce TWO sets of queries:

1. PRO — queries that would surface evidence SUPPORTING the claim.
2. CONTRA — queries that would surface evidence AGAINST it. Do NOT negate the
   claim syntactically. First state the strongest opposing thesis a competent
   critic would defend (the steelman), then write queries for THAT. If the
   claim is compound, identify the weakest load-bearing assertion and target it.

Rules:
- Default database is PubMed: use Boolean blocks, MeSH terms where apt, field
  tags ([tiab], [Mesh]). For Europe PMC never use MESH: — map MeSH headings
  to KW: instead (the MESH: field breaks on multi-word headings).
- OpenAlex ANDs every word of the query: 2–4 core keywords, no more. Each
  extra word narrows the search — a natural-language sentence returns zero.
- Build synonym OR-blocks for the core concepts; do not over-constrain.
- No year window unless the claim is intrinsically time-bound.
- Expect to iterate: the search tool returns the total hit count. ~0 hits
  means too narrow, tens of thousands means too broad. Refine and retry.
- If the claim originates from (or names) an identifiable paper, plan a
  snowball on it besides the keyword queries: works CITING the pivot are
  where replications and rebuttals live — the highest-yield contra move.

Return: the steelman statement, then each query labelled with its stance
(pro/contra) and target database (or snowball + pivot DOI + direction).
""")


SELECT_RECORDS = _p(
    "select_records", "1.1.0", "Records → shortlist for full-text reading",
    """
You are screening search results (title + abstract) to decide which papers
deserve full-text reading for the claim below.

CLAIM: {claim}
STANCE OF THIS BATCH: {stance}

Selection criteria, in order:
1. DIRECTNESS — the paper tests or reviews the claim itself, not a neighbour.
2. EVIDENCE LEVEL — systematic reviews and meta-analyses > primary empirical
   studies > narrative reviews > opinion/editorial.
3. RECENCY breaks ties.

Rules:
- Judge only on title + abstract; do not guess unstated content.
- Select AT MOST {max_select} records per stance. Fewer is fine: a paper you
  would not actually read is noise in the trace.
- An abstract that already contradicts the batch stance is still selectable —
  evidence goes where it goes, not where the query pointed.
- A record flagged possible_duplicate_of is the same paper under another DOI
  (publisher copy vs preprint sibling). Shortlist ONE copy: prefer the
  version of record for citing, but note that the sibling is often the
  retrievable full text when the publisher copy is paywalled.
- Log every selection with one reason, and log notable exclusions (papers that
  look topical but fail a criterion) with theirs — the trace must show why the
  shortlist is what it is.

Return: selected records (identifier + reason) and notable exclusions
(identifier + reason).
""")


VERIFY_FULLTEXT = _p(
    "verify_fulltext", "1.0.0", "Full text → passages bearing on the claim",
    """
You are reading one paper's full text to determine what it says about a claim.

CLAIM: {claim}

Extract every passage that BEARS ON the claim — supporting, contradicting, or
qualifying it. For each passage report:
- QUOTE: the passage verbatim, trimmed to the load-bearing sentence(s);
- LOCATION: the section heading it sits under (or "abstract"/"conclusion");
- BEARING: supports_directly | supports_indirectly | contradicts | qualifies;
- WHY: one sentence linking the quote to the claim, without going beyond what
  the text licenses.

Rules:
- Verbatim means verbatim. If you cannot quote it, it is not evidence.
- Never infer beyond the text; a result about mice is a result about mice.
- Note the study type and any limitation the authors themselves flag.
- If the paper turns out to be irrelevant, say so and quote nothing.

Return: paper verdict (supports / contradicts / mixed / irrelevant) + the
passage list.
""")


RENDER_VERDICT = _p(
    "render_verdict", "1.0.0", "Evidence → graded verdict + dossier",
    """
You are rendering the final verdict on a claim after both search passes (pro
and steelman-contra) and full-text verification.

CLAIM: {claim}

Grade the claim as exactly one of:
- SUPPORTED — direct evidence for, no credible contradicting evidence found;
- CONTESTED — credible evidence on both sides, or strong qualifications;
- UNSUPPORTED — direct evidence against outweighs evidence for;
- NO EVIDENCE — neither pass surfaced evidence that bears on the claim.
  Absence of evidence is NOT evidence of absence: say what was searched and
  not found, never convert silence into refutation.

Rules:
- Weigh the contra evidence first, then ask whether the pro evidence survives.
- State confidence (high/moderate/low) and what drives it (evidence level,
  consistency, directness).
- Cite ONLY with procedural tokens: [R:doi] (or [R:url:...] when a record has
  no DOI), where the identifier is one of a record seen in this run. Never
  write author names, years or titles into a citation yourself — the tokens
  are resolved downstream from record metadata.
- The dossier must let a reader disagree with you: claim, steelman, what was
  searched, what was read, key passages per side, verdict, confidence, and
  what evidence would change the verdict.

Return: the dossier, verdict first.
""")


VERIFY_CLAIM = _p(
    "verify_claim", "1.2.0", "Master protocol — full claim verification run",
    """
Run a full Contrarian verification of a claim against the scientific
literature. You orchestrate; the server searches, retrieves and logs. Every
judgment you make must be logged to the trace — an unlogged step is a step
that did not happen.

THE CLAIM: {claim}

1. START — call start_run(claim) and keep the run_id for every later call.
2. FORMULATE — follow prompt formulate_queries: steelman + pro/contra queries.
3. SEARCH — call search() for each query (both stances, PubMed first, other
   databases when coverage demands it). Use the returned total to iterate on
   bad queries. Search the contra stance with the same effort as the pro.
   When a pivotal paper surfaces (or the claim names one), call
   snowball(run_id, doi, 'citing', stance) on it: replications and rebuttals
   cite the paper they answer, and keyword queries routinely miss them.
   Records flagged possible_duplicate_of are the same paper under another
   DOI — treat them as one.
4. SELECT — follow prompt select_records; log shortlist and notable
   exclusions with log_selection().
5. READ — for each selected record call get_fulltext(doi). If retrieval fails
   with url_only, note the link in the trace and move on — do not substitute
   the abstract for the full text silently; mark any abstract-only judgment
   as such.
6. VERIFY — follow prompt verify_fulltext for each text; log every paper's
   passages with log_verification().
7. VERDICT — follow prompt render_verdict; write the dossier with [R:...]
   tokens only.
8. FINISH — call finish_run(run_id, verdict, dossier). The server resolves
   your tokens into real references and appends two procedural blocks
   computed from the trace: run statistics (how many records were seen, how
   many full texts were actually read vs judged on abstract only) and the
   numbered reference list (author, year, DOI link).
9. REPORT — your final message to the user must contain, verbatim from
   finish_run's response: the trace URL, the run-statistics block, and the
   full reference list. Summarize the findings in your own words if you
   wish, but never paraphrase, renumber or omit the references and the
   statistics — they are the auditable part of the answer. Report any
   unresolved tokens as failures, not as references.
""")


STEP_PROMPTS = [FORMULATE_QUERIES, SELECT_RECORDS, VERIFY_FULLTEXT, RENDER_VERDICT]
ALL_PROMPTS = STEP_PROMPTS + [VERIFY_CLAIM]


def get(name: str) -> dict | None:
    return next((p for p in ALL_PROMPTS if p["name"] == name), None)


def versions() -> dict:
    """{prompt name: version} — stamped into every run's trace."""
    return {p["name"]: p["version"] for p in ALL_PROMPTS}
