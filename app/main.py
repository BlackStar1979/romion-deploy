"""
romion-deploy - bounded deploy channel (FastAPI).

A typed deploy vocabulary over allowlisted docker-compose stacks. Runs on the
VPS as the `deploy` user (member of the docker group). No shell, no arbitrary
paths, no arbitrary commands — callers reference a stack by name and pick an
action from a fixed set.

Auth: app-layer bearer (DEPLOY_AUTH_TOKEN) AND intended to sit behind Cloudflare
Access Service Auth, mirroring romion-llm-router. Either layer alone rejects
anonymous callers; together they are defense in depth.
"""

from __future__ import annotations

import hmac
import os

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from app.compose import run_compose
from app.config import ConfigError, load_stacks, resolve_stack
from app.release import ReleaseError, apply_release, list_releases, rollback

app = FastAPI(title="romion-deploy", version="0.1.0")

_TOKEN = os.environ.get("DEPLOY_AUTH_TOKEN", "").strip()


async def require_auth(request: Request) -> None:
    if not _TOKEN:
        # Fail closed: refuse to serve if no token is configured.
        raise HTTPException(status_code=503, detail="deploy channel not configured (no DEPLOY_AUTH_TOKEN)")
    provided = request.headers.get("authorization", "")
    if not hmac.compare_digest(provided, f"Bearer {_TOKEN}"):
        raise HTTPException(status_code=401, detail="unauthorized")


def _resolve_or_404(stack: str) -> str:
    try:
        return resolve_stack(stack)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown stack: {stack}")
    except ConfigError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/v1/status")
async def status(_: None = Depends(require_auth)) -> dict:
    try:
        stacks = sorted(load_stacks().keys())
        return {"status": "ok", "service": "romion-deploy", "stacks": stacks}
    except ConfigError as e:
        return {"status": "config_error", "error": str(e)}


@app.get("/v1/stacks")
async def stacks(_: None = Depends(require_auth)) -> dict:
    return {"stacks": load_stacks()}


@app.post("/v1/compose/{stack}/up")
async def compose_up(stack: str, _: None = Depends(require_auth)) -> dict:
    return run_compose(_resolve_or_404(stack), "up")


@app.post("/v1/compose/{stack}/down")
async def compose_down(stack: str, _: None = Depends(require_auth)) -> dict:
    return run_compose(_resolve_or_404(stack), "down")


@app.post("/v1/compose/{stack}/restart")
async def compose_restart(stack: str, _: None = Depends(require_auth)) -> dict:
    return run_compose(_resolve_or_404(stack), "restart")


@app.get("/v1/compose/{stack}/ps")
async def compose_ps(stack: str, _: None = Depends(require_auth)) -> dict:
    return run_compose(_resolve_or_404(stack), "ps")


@app.get("/v1/compose/{stack}/logs")
async def compose_logs(stack: str, tail: int = 200, _: None = Depends(require_auth)) -> dict:
    return run_compose(_resolve_or_404(stack), "logs", tail=tail)


class ReleaseIn(BaseModel):
    bundle_b64: str
    sha256: str
    keep: int = 5


@app.post("/v1/release/{stack}")
async def release_apply(stack: str, body: ReleaseIn, _: None = Depends(require_auth)) -> dict:
    """Stage a content-addressed build-context bundle into the stack's release
    dir and flip `current`. Follow with POST /v1/compose/{stack}/up to build+run."""
    base = _resolve_or_404(stack)
    try:
        return apply_release(base, body.bundle_b64, body.sha256, keep=body.keep)
    except ReleaseError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/v1/release/{stack}")
async def release_list(stack: str, _: None = Depends(require_auth)) -> dict:
    return list_releases(_resolve_or_404(stack))


@app.post("/v1/release/{stack}/rollback")
async def release_rollback(stack: str, _: None = Depends(require_auth)) -> dict:
    """Re-point `current` to the previous release. Follow with compose up to apply."""
    base = _resolve_or_404(stack)
    try:
        return rollback(base)
    except ReleaseError as e:
        raise HTTPException(status_code=400, detail=str(e))
