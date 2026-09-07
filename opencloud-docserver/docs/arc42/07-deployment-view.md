# 07 — Deployment View

## Production (the live stack — `~/opencloud-compose`)

```
cloud.graphwiz.ai ──▶ Caddy (TLS) ──▶ opencloud        :9200  (file cloud, IDM admin)
              editor ──▶ Caddy ──▶ python-docserver  :8082  (this system; image opencloud-docserver:prod)
                                   tika              :9998  (text extraction; bash-TCP healthcheck)
                                   collaboration     gRPC   (auto-registers docserver as WOPI app)
```

- **Build:** `docker compose build python-docserver` — image = python-slim + uv + playwright-free
  runtime deps (`uv sync --frozen --no-dev`) + `fonts-dejavu-core` (no fonts ⇒ blank PDF glyphs).
- **Validation before deploy:** build → throwaway port → curl `/health` + discovery → then
  `docker compose up -d --force-recreate` (plain `restart` does NOT pick up env/compose changes).
- **State:** `opencloud-config` / `opencloud-data` named volumes; docserver keeps its SQLite + files
  in its own data volume. Registration files (`app-registry.yaml`) live in the opencloud volume.
- **Smoke after deploy:** `/health`, WOPI discovery XML, Caddy 200, create+export roundtrip
  (`X-Export-Engine: weasyprint`, `%PDF-1.7` magic).

## Staging (`~/opencloud-compose-staging`, :9201)

Mirror topology with `docserver-py` + `ocstaging` containers; risky experiments (config A/B tests,
healthcheck changes) land here first. Its `/etc/opencloud` volume now mirrors the live
`app-registry.yaml`.

## CI/CD (GitHub Actions, repo root = `server/`)

| Workflow | Trigger | Gates |
|----------|---------|-------|
| `docserver.yml` | PR/push touching `opencloud-docserver/**` (register/harness gates run from wo-test-harness) | `uv sync --frozen`, full pytest suite (incl. browser e2e, chromium), graph drift gate, register all-82 |
| `conformance.yml` | PR paths + weekly schedule | Rust unit tests, drift gate, cross-engine fidelity vs LibreOffice, OnlyOffice oracle report |
| `docker.yml` | push main | container build |

Config/env provenance: `config.toml` + env overlay (`WO_JWT_SECRET`, `DOCSERVER_AGENTS`, …) —
change deployment values via compose env, then `up -d --force-recreate`.
