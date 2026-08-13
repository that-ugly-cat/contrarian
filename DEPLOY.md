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
# edit .env: ADMIN_PASSWORD, JWT_SECRET, PUBLIC_URL, UNPAYWALL_EMAIL,
# PAPER2MD_URL (use the *internal* address, e.g. http://172.17.0.1:8008,
# so conversions skip the proxy's ~100s limit), publisher TDM keys if any.

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
2. `/admin` → issue an API key.
3. Register the MCP server in your client:
   `claude mcp add --transport http contrarian https://contrarian.borant.eu/mcp --header "X-API-Key: KEY"`
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
