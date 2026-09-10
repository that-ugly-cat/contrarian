# Contrarian — User Guide

Contrarian checks a claim against the scientific literature, and checks the strongest *opposite* claim with the same effort. You state a claim; a model in a chat window drives the pipeline through Contrarian's tools; the server searches, retrieves full texts, logs every step and assembles the references. Every judgement — how to phrase a query, which papers deserve reading, what a passage says, what the verdict is — stays with the model in the conversation, where you can watch it and interrupt. What you get back is a **dossier** and a **trace**: a graded verdict, and the audit trail that lets you disagree with it.

---

## 1. Getting started

1. Open [contrarian.borant.eu](https://contrarian.borant.eu). The front page is public and does not look at who is reading it: it lists every tool, protocol prompt and library module, each with a plain-language recap and its own live source.
2. **Sign in** at `/app` with your Borant ID — the same account as the other borant tools. The first sign-in creates your profile here; nothing else is needed from you.
3. **Ask Spit for an API key.** Keys are issued in the admin panel, not self-served, and every key is issued *to a person*: a run made with it belongs to you, and the traces page shows yours and nobody else's. Your keys are listed under **your keys** (`/me`), each starting with `ctr_`.
4. **Connect the key to your chat client** (§8). That is how a verification actually runs; the web app is where you read what happened.
5. Optionally, on **your keys**, store the institutional credentials that open subscription full text (§5).

## 2. What counts as a claim

A **claim** is one falsifiable assertion, stated as a sentence: *"vitamin C prevents the common cold"*, *"AI-generated disinformation is more persuasive than human-generated"*. One run is one claim is one trace page, and the claim text is stored verbatim and re-injected into every step of the protocol — so a vague claim produces a vague run.

- **Compound claims get split, not averaged.** The protocol tells the model to identify the weakest load-bearing assertion and target that, but a claim with two independent arms ("X and Y both lower Z") is cleaner as two runs.
- Nothing in the protocol distinguishes an empirical claim from a normative one. Contrarian will search the literature for either; whether the literature can settle it is your call, made before the run rather than after.

## 3. The protocol, and the version that ran

The workflow is written down as versioned prompt text, served to the model and shown to you unchanged on the site. The master protocol is **verify_claim v1.2.0**; the step prompts it binds are `formulate_queries` v1.1.0, `select_records` v1.1.0, `verify_fulltext` v1.0.0 and `render_verdict` v1.0.0. Every trace is stamped with the versions it ran under, so a dossier is reproducible: claim plus protocol version plus records seen gives the verdict.

The nine steps it prescribes:

| # | Step | What happens |
|---|---|---|
| 1 | START | `start_run(claim)` opens the trace and returns the protocol inline, because not every client can fetch prompts |
| 2 | FORMULATE | the model states the **steelman** — the strongest opposing thesis a competent critic would defend — then writes pro and contra queries for both sides |
| 3 | SEARCH | one call per query, both stances, plus snowballing on any pivotal paper |
| 4 | SELECT | a shortlist per stance, with one reason per paper **and** reasons for notable exclusions |
| 5 | READ | full text for each shortlisted paper; an abstract-only judgement must be marked as such |
| 6 | VERIFY | verbatim passages per paper, each with its location, its bearing on the claim and one sentence of justification |
| 7 | VERDICT | one of `supported`, `contested`, `unsupported`, `no_evidence`, with stated confidence and what would change it |
| 8 | FINISH | the server resolves the citations and appends run statistics and the reference list |
| 9 | REPORT | the model must hand you the trace URL, the statistics block and the reference list verbatim |

Selection is capped per stance — five papers by default — on the grounds that a paper you would not actually read is noise in the trace. The contra pass is the load-bearing part, and it is default-on: contrary evidence uses different vocabulary from supporting evidence, so a search engine surfaces it only if something forces the queries to speak that vocabulary. A syntactic negation of the claim does not do that; a steelman does.

## 4. Searching the literature

Three databases, queried in their own native syntax, relevance-ranked, capped:

| Database | Use it for | Syntax notes |
|---|---|---|
| **PubMed** | the default; biomedical coverage | Boolean blocks, MeSH terms, field tags (`[tiab]`, `[Mesh]`) |
| **Europe PMC** | biomedical plus preprints | never `MESH:` — it collapses on multi-word headings; use `KW:` |
| **OpenAlex** | anything outside biomedicine | every word is ANDed, so 2–4 core keywords; a natural-language sentence returns zero |

Each search returns the database's **total hit count** alongside up to `limit` records — 25 by default, 100 at most. The count is feedback, not decoration: near-zero means the query is too narrow, tens of thousands means too broad, and the protocol tells the model to refine and retry. A malformed query comes back carrying the database's own complaint so it can be fixed, and the failed attempt stays in the trace. Abstracts are truncated at 2,500 characters with the cut declared, because OpenAlex reconstructs abstracts from an index and occasionally returns an entire article as one.

**Snowball** is the second retrieval verb: citation chasing on a pivot DOI through OpenAlex, forward (`citing` — works that cite the pivot) or backward (`cited` — the pivot's own reference list), sorted by citation count so the influential answers surface first. Forward snowballing is the highest-yield contra move available when a claim originates from a known paper, because replications and rebuttals cite the paper they answer and keyword queries miss them systematically. It needs a DOI: a record identified only by a URL cannot be chased.

A record whose title matches one already seen in the run arrives flagged `possible_duplicate_of` — a publisher copy and its preprint sibling are one paper, to be shortlisted once. The sibling is often the copy whose full text is actually retrievable.

## 5. Full text: what arrives, and what does not

Given a DOI, the server walks a five-rung ladder: Europe PMC full-text XML, then open-access PDFs from Unpaywall and OpenAlex (repository copies first, since publisher copies sit behind bot walls more often), then landing pages resolved through the `citation_pdf_url` tag most publishers emit, then publisher text-and-data-mining APIs, then same-titled open-access siblings under a different DOI. PDFs are converted to markdown with the bibliography stripped — the model reads the article, not its reference list.

Three outcomes, all logged:

- **ok** — text retrieved, with the provider that produced it named in the trace.
- **url_only** — no text, but a link you can open by hand. The protocol forbids quietly substituting the abstract: an abstract-only judgement must be labelled as one, and the run statistics count those separately.
- **failed** — nothing found at all.

Two guards worth knowing about. **Every retrieved text is verified against the expected title** of the record that was requested, as a contiguous line match rather than a bag of words: open-access metadata is dirty, and repositories have been caught declaring an entirely different paper as the open copy of a DOI. A candidate that does not match is discarded with a note and the ladder continues. And **nothing is archived**: the text is fetched, handed to the model and dropped — the trace keeps the outcome and the quoted passages, never the article, because lawful private use does not stretch to a server that stores and re-serves copyrighted text.

**Subscription content runs on your licence, not the server's.** An institutional licence covers the people it names, so the Elsevier and Wiley rungs use credentials carried on *your* API key, set by you under **your keys**. Nothing there is ever displayed back — a blank field keeps what is stored, and a checkbox clears all three — and a key carrying none stops at open access and says so in the trace notes. The Elsevier API key alone is refused when a server asks; the institutional token your library issues alongside it is the part that opens the door. Springer's open-access endpoint is configured centrally, because nobody needs a licence to read open access.

## 6. The trace

A **run** is one claim and its append-only event log, listed at `/app`. Five kinds of event: `search` (database, query, stance, hit count and the record metadata returned), `selection` (what was shortlisted and what was deliberately excluded, with reasons), `fulltext` (status, provider, link, whether the title could be verified, any notes), `verification` (one paper's verdict and its quoted passages) and `finish`.

Read the trace as the actual product. It shows the queries that ran and the ones that failed, the papers left out and why, which judgements rest on a full text and which on an abstract, and every warning the server raised along the way — a citation key never seen in this run's searches, a text that could not be checked against its title, an unresolved citation token.

Traces are private to whoever produced them, and administrator rights do not widen that: a trace is content, and content stays with the person who made it. A trace can be published read-only as an unguessable, revocable link (`/r/{token}`) from its own page — the one deliberate exception, so that a verified claim can cite its own audit trail. The method stays public either way: the catalogue and every prompt are readable without an account, rendered from the running process rather than from documentation that can drift out of date.

## 7. The dossier and its references

The dossier is the model's synthesis: claim, steelman, what was searched, what was read, the key passages on each side, the graded verdict, the confidence and what evidence would change it. Two blocks are appended by the server, computed from the trace rather than from the model's account of itself:

- **Run statistics** — searches per stance, total hits, unique records, how many were shortlisted, how many full texts were obtained versus link-only versus not found, and how many papers were judged on the abstract alone.
- **References**, numbered.

The model cannot write a citation. It cites with opaque tokens (`[R:10.1234/abc]`), and the server resolves each one against the closed set of records this run's searches actually returned; a token pointing outside that set is reported as a failure and left visible, never rendered as a reference. An invented reference here is not forbidden, it is unavailable — a different and stronger guarantee, and the reason the numbered list is the part of the answer you should trust most.

## 8. Driving Contrarian from a chat (MCP)

Contrarian has no button that starts a verification. It is a set of tools an AI assistant picks up, so the run happens inside a conversation you can read and stop.

Connect it once, with your key. In Claude Code:

```
claude mcp add --transport http contrarian https://contrarian.borant.eu/mcp --header "X-API-Key: YOUR_KEY"
```

Any MCP-capable client works the same way: endpoint `https://contrarian.borant.eu/mcp`, key in an `X-API-Key` header. Clients that cannot send custom headers — ChatGPT's custom connectors, for one — can use `https://contrarian.borant.eu/mcp/k/YOUR_KEY/` instead, where the key travels in the URL and can therefore end up in access logs: use a separate key per client and revoke rather than share.

Then ask for the `verify_claim` prompt with your claim — in Claude Code, `/mcp__contrarian__verify_claim`. Asking in plain words ("verify this claim with Contrarian") also works, because `start_run` returns the entire protocol in its response for exactly that case.

Seven tools are exposed, and the division is the point: `search`, `snowball` and `get_fulltext` do the fetching; `log_selection` and `log_verification` are where the model deposits its reasoning, because a judgement that was not logged is a judgement that did not happen; `start_run` and `finish_run` open and close the trace. The surface reaches exactly what you reach — the key is your identity, including which publisher entitlement a run may use. There is also a stateless mirror for scripts, `POST /api/search` and `POST /api/fulltext` on the same key, when you want records or one full text without opening a run.

Watch the conversation while it works. If the queries are wrong, if the shortlist skips the paper you had in mind, if the steelman is a straw man — say so mid-run. That is the whole reason the judgement steps live in the chat and not in the server.

## 9. What Contrarian does not do

- **It does not settle whether a claim is true.** A verdict is one model's graded reading of the papers that one set of queries happened to surface, under a stated protocol. `contested` and `no_evidence` are honest outcomes, and absence of evidence is never converted into refutation.
- **A logged verification is a record of somebody's judgement, not a fact.** The quote is verbatim and checkable against the paper; the bearing assigned to it, the paper verdict and the final grade are interpretation. They are logged so that you can overturn them, which is not the same as being right.
- **It is not a systematic review.** Searches are capped and relevance-ranked, never exhaustive, so no coverage claim can be made from a run. For a review protocol with screening and reconciliation, use LSSR instead.
- **It does not read what it cannot retrieve**, and it does not weigh the standing of a journal, check whether a paper has been retracted, or notice that two apparently independent studies are the same cohort published twice. It reports the paywall; the rest of that work is reading.
- **It does not remember.** No paper is archived and no knowledge carries across runs: verify the same claim tomorrow and you get another trace, possibly another verdict.

## 10. Data protection

Contrarian processes whatever claim you hand it, and a claim is a sentence you wrote.

- **What is stored on the server**: your traces — claim text, queries, hit counts, record metadata, your model's selection reasons, retrieval outcomes, quoted passages and the dossier — plus your API keys. Publisher credentials on a key are encrypted at rest and never rendered back to anyone, the administrator included. Full texts are not stored.
- **What reaches external services during a run**: your queries go to PubMed (NCBI), Europe PMC and OpenAlex; retrieval consults Unpaywall, OpenAlex, Crossref and publisher APIs and fetches from repository and publisher servers. All of them see the words you searched with, and query wording derives from your claim — so a claim naming an identifiable person, an unpublished dataset or a confidential manuscript is a claim you are typing into third-party search logs.
- **What reaches a language model**: everything the run puts in context — abstracts, retrieved full texts, quotes, the dossier — passes through whichever model your chat client runs, on that provider's infrastructure. Contrarian holds no model key of its own, so that exposure is your client's and it is yours to account for.
- **Copyright**: retrieved texts are read in context and dropped. What you do with what a run showed you is on you, and the subscription rungs ran under your institutional licence with its own terms attached.
- **Compliance** with GDPR, your institutional requirements and the conditions of your ethics approval is the researcher's responsibility, not the tool's. A shared trace link is readable by anyone holding it — read what the claim and the quotes actually reveal before you mint one.

## 11. Good practices

- **State the claim you actually mean.** It anchors every query, every screening decision and the verdict. "Screens harm children" and "more than two hours of daily recreational screen time is associated with lower measured attention in 8–12-year-olds" are not the same run.
- **Read the trace before you trust the verdict.** Start with the exclusions and the abstract-only count. A confident verdict resting on two abstracts is a hypothesis about the literature, not a finding about it.
- **Interrupt.** You are in the conversation for a reason: bad queries, a straw-man steelman and a shortlist that misses the obvious paper are all cheap to fix mid-run and expensive to discover in the dossier.
- **Snowball anything pivotal.** If the claim comes from a paper, or a review turns out to be the reference point, chase forward from it — that is where replications and rebuttals live, and keyword queries do not reach them.
- **Set your institutional credentials once.** Much of the relevant literature sits behind Elsevier and Wiley, and the alternative to a licence is an abstract-only judgement.
- **Treat a verdict as a starting position, not a citation.** Use the dossier to argue with yourself, mint a share link when a claim needs to show its work, and keep the reading where it has always been: with you.
