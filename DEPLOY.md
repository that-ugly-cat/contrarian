# Deploying Contrarian

Target: the borant VPS pattern — Docker behind Caddy, one subdomain per tool.

## Prerequisites

- Docker + compose plugin
- A reverse proxy terminating TLS (Caddy shown)
- Optional but recommended: a running paper2md instance (PDF conversion)

## Steps

```bash
git clone https://github.com/that-ugly-cat/contrarian
cd contrarian
cp .env.example .env
# edit .env: ADMIN_PASSWORD, JWT_SECRET, TDM_SECRET, PUBLIC_URL,
# UNPAYWALL_EMAIL, PAPER2MD_URL (use the *internal* address, e.g.
# http://172.17.0.1:8008, so conversions skip the proxy's ~100s limit),
# SPRINGER_API_KEY if you have one. Elsevier and Wiley credentials do NOT go
# here — they belong to an institutional licence, so each API key carries its
# own; set them per key in /admin (see "First run").

GIT_COMMIT=$(git rev-parse --short=12 HEAD) docker compose build
docker compose up -d
curl -s localhost:8014/health
```

## Caddy

Alphabetical order by subdomain in the Caddyfile:

```
contrarian.borant.eu {
    reverse_proxy localhost:8014
}
```

`/mcp` uses streamable HTTP (POST + SSE responses); Caddy proxies it without
extra configuration.

## First run

1. Open `https://contrarian.borant.eu/login`, enter `ADMIN_PASSWORD`.
2. `/admin` → issue an API key. If the holder of that key is covered by an
   institutional TDM licence, open its "TDM credentials" panel and enter the
   Elsevier API key + institutional token and/or the Wiley TDM token. Without
   them the key still works: it walks the open-access ladder, which needs no
   licence. The credentials are encrypted with `TDM_SECRET` and never shown
   again — rotating that secret makes stored credentials unreadable, and they
   have to be entered afresh.
3. Register the MCP server in your client:
   `claude mcp add --transport http contrarian https://contrarian.borant.eu/mcp --header "X-API-Key: KEY"`

   Clients that cannot send custom headers (e.g. ChatGPT custom connectors,
   which accept only OAuth or no-auth) can use the capability-URL variant:
   register `https://contrarian.borant.eu/mcp/k/KEY` with authentication
   "none". Same key table, same revocation from /admin — mint a dedicated
   key per client so it stays individually revocable. The key travels in
   the URL path, so it may land in access logs: treat it as revocable, not
   as unexposable.
4. Smoke test:
   `curl -s -X POST https://contrarian.borant.eu/api/search -H "X-API-Key: KEY" -H "Content-Type: application/json" -d '{"database":"pubmed","query":"aspirin[tiab] AND headache[tiab]","limit":3}'`

## Update

```bash
git pull
GIT_COMMIT=$(git rev-parse --short=12 HEAD) docker compose build
docker compose up -d
```

## Backup

`data/contrarian.db` (API keys + traces). No other state — full texts are
never stored by design.

## Behind an SSO gate (`AUTH_MODE=gateway`)

Optional, and off unless you switch it on. It moves **one** boundary: the admin
password that guards `/runs` and `/admin` is replaced by an upstream
`forward_auth` gate. Everything else stays exactly where it was.

**The glass box stays open.** `/`, `/piece/*` and `/health` are public because
they mirror a public repo — hiding the method would add friction, not security —
and `/r/{token}` stays public because a shared trace is meant to be readable by
someone who has no account here and should not need one.

**The key-facing surface does not move either.** `/mcp*` and `/api/*` keep their
own `X-API-Key`, checked in the middleware before any handler runs. That is not
just a convenience for clients with no cookie: the TDM credentials ride on the
key row, so *which key called* is the question the audit trail has to answer,
and a domain session cannot answer it.

**`local` stays the default.** An app that believes `X-Borant-Sub` with nothing
in front of it lets in anyone who sends that header.

```
contrarian.borant.eu {
    @pubbliche path / /piece/* /r/* /health /api/* /mcp /mcp/* /login /logout
    handle @pubbliche {
        import noforge
        import nocookie
        reverse_proxy localhost:8014
    }
    handle {
        import borantid
        reverse_proxy localhost:8014
    }
}
```

There is no user table here and nothing to map: the gate answers one question,
"is this the admin", and a grant for this host is what makes the answer yes.
`ADMIN_PASSWORD` should stay set — it is what `AUTH_MODE=local` falls back to.

`BORANT_TRUSTED_PROXY` is the second lock and the setting people get wrong.
Under Docker the container does not see `127.0.0.1` but a bridge gateway. Read
it off reality rather than guessing:

```bash
curl -s -o /dev/null http://127.0.0.1:8014/health && docker logs contrarian-contrarian-1 2>&1 | tail -1
```

Rollback, two lines:

```bash
sed -i 's/^AUTH_MODE=gateway/AUTH_MODE=local/' .env
docker compose up -d
```
