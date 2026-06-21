# romion-deploy

Bounded deploy channel for the ROMION VPS. A small typed HTTP service that an
agent (via TinyPyMCP) calls to deploy/manage allowlisted docker-compose stacks —
**no shell, no arbitrary paths, no arbitrary commands**.

## Why it exists

The mission needs an agent to stand up containers on the VPS. The existing
`romion-llm-router/worker` is deliberately unable to do docker (read-only,
no socket). Rather than give the agent raw SSH-as-ubuntu (max blast radius),
this is a separate bounded capability running as the dedicated `deploy` user.

## Model

- Runs as a **host systemd service** as user `deploy` (in the docker group), so
  it uses docker natively — the docker socket is NOT mounted into any container
  (per VPS doctrine).
- Listens on `127.0.0.1:8091` only. Public access is via a Cloudflare Tunnel
  route + **Cloudflare Access Service Auth**, like `romion-llm-router`.
- App-layer **bearer auth** (`DEPLOY_AUTH_TOKEN`) on top, defense in depth.
  Fails closed: no token → 503.
- Acts only on stacks declared in `app/stacks.json` (name → dir). Callers pass a
  stack NAME, never a path.

## API

```
GET  /v1/status                  -> service ok + stack names
GET  /v1/stacks                  -> allowlisted stacks
POST /v1/compose/{stack}/up      -> docker compose up -d
POST /v1/compose/{stack}/down    -> docker compose down
POST /v1/compose/{stack}/restart -> docker compose restart
GET  /v1/compose/{stack}/ps      -> docker compose ps
GET  /v1/compose/{stack}/logs    -> docker compose logs --tail N
```

All require `Authorization: Bearer <DEPLOY_AUTH_TOKEN>`.

## Layout

```
app/main.py      FastAPI app + bearer auth (thin web layer)
app/compose.py   bounded compose runner (no shell, timeout, output caps)
app/config.py    stack allowlist loader/validator
app/stacks.json  the allowlist (empty by default — safe)
deploy/romion-deploy.service  systemd unit template (runs as deploy)
requirements.txt fastapi + uvicorn
```

## Deploy outline (executed step-by-step with the operator)

1. Copy this dir to `/home/deploy/romion-deploy` (owned by deploy).
2. Create venv + `pip install -r requirements.txt`.
3. Write `/home/deploy/romion-deploy/.env` with `DEPLOY_AUTH_TOKEN=...` (chmod 600).
4. Install + enable `deploy/romion-deploy.service`.
5. Add a Cloudflare Tunnel route -> `http://127.0.0.1:8091` behind CF Access
   Service Auth.
6. Register real stacks in `app/stacks.json` and reload.

The TinyPyMCP side is a client that calls this API with the bearer token + CF
Access service headers, all read from a server-side config (never as tool args).
