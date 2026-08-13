# Contrarian

**Claim verification against the scientific literature, with the reasoning
kept visible — including the search for the opposite claim.**

Give a model a claim ("pigeons have three legs and therefore an aerodynamic
advantage") and Contrarian is the infrastructure it uses to check it properly:
search the literature for evidence *for*, search with the same effort for the
strongest case *against* (a steelman, not a syntactic negation), read the full
texts, and render a graded verdict — with every step logged to an auditable
trace and every citation assembled from record metadata, so an invented
reference is structurally impossible.

## Design

The pipeline splits along an agency line:

| step | who | where |
|---|---|---|
| claim → pro + steelman-contra queries | model | conversation |
| query → records (title + abstract) | **server** | PubMed, Europe PMC, OpenAlex |
| shortlist with reasons | model | conversation, logged |
| DOI → full text → markdown | **server** | OA ladder + publisher TDM + paper2md |
| reading, entailment, synthesis | model | conversation, passages logged |
| reference assembly | **server** | procedural, from run metadata |
| graded verdict | model | conversation, logged |

The server does I/O; judgment stays in conversation where a human can see and
interrupt it. The model's own steps are governed by **versioned protocol
prompts** served over MCP — every trace records which protocol version it ran
under, so a dossier is reproducible: claim + protocol + records seen → verdict.

**The glass box.** The web UI shows, for every tool, prompt and library
module: a plain-language recap and its live source (`inspect.getsource()` of
the running process, never a copy), with the deployed commit hash in the
footer. Public what is method, private what is content: catalog and prompts
need no login; traces do.

**Verdicts are graded**: `supported` / `contested` / `unsupported` /
`no_evidence` — absence of evidence is an outcome, never converted into
refutation.

**No paper archive.** Full texts are fetched, converted, handed to the model
in context and dropped. The trace keeps outcomes and quoted passages, not
articles — lawful private use does not stretch to a server that stores and
re-serves copyrighted text.

## MCP surface

Six tools: `start_run`, `search`, `get_fulltext`, `log_selection`,
`log_verification`, `finish_run`. Five prompts: the four step prompts plus
`verify_claim`, the master protocol. Streamable HTTP at `/mcp`, gated by
`X-API-Key` (issued in `/admin`).

Claude Code:

```bash
claude mcp add --transport http contrarian https://contrarian.borant.eu/mcp --header "X-API-Key: YOUR_KEY"
```

Then: `/mcp__contrarian__verify_claim` with the claim, and the model runs the
protocol.

## REST mirrors

`POST /api/search` `{database, query, limit, year_from?, year_to?}` and
`POST /api/fulltext` `{doi}` — stateless, same `X-API-Key`, for scripts and
curl.

## Run it

```bash
cp .env.example .env   # set ADMIN_PASSWORD at minimum
docker compose build --build-arg GIT_COMMIT=$(git rev-parse --short=12 HEAD)
docker compose up -d
```

Port 8014. State: `data/contrarian.db` (API keys + traces only).

## Lineage

Derived from the [borant](https://borant.eu) toolchain: search adapters and
the full-text ladder from [LSSR](https://github.com/that-ugly-cat/lssr)
(including its hard-won fixes — Europe PMC `MESH:` → `KW:` mapping, canonical
author handling, repository-first OA resolution), PDF conversion via
[paper2md](https://github.com/that-ugly-cat/paper2md), procedural citations
from LSSR's synthesis step. AGPL-3.0, like its siblings.
