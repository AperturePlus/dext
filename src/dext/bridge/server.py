"""aiohttp adapter for the fixed userscript HTTP contract (overview §4, api.ts).

Pure HTTP↔domain boundary: read body as forced UTF-8, call HumanFetcherBridge /
DecisionCenter, serialize back to the TS interface shapes with ensure_ascii=False.
No job logic lives here.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from aiohttp import web

from dext.bridge.decision import DecisionCenter, PendingDecision
from dext.bridge.fetcher import HumanFetcherBridge
from dext.bridge.health import FrontendHealthSnapshot
from dext.bridge.queue import FetchJob, JobContext, QueueStats
from dext.types import FetchAction, PaginationState
from dext.url_policy import has_explicit_port

logger = logging.getLogger(__name__)

API_PREFIX = "/api"

# Type-safe application storage keys (aiohttp >=3.9 AppKey; avoids NotAppKeyWarning).
BRIDGE: web.AppKey[HumanFetcherBridge] = web.AppKey("bridge", HumanFetcherBridge)
DECISION_CENTER: web.AppKey[DecisionCenter] = web.AppKey("decision_center", DecisionCenter)
START_TIME: web.AppKey[float] = web.AppKey("start_time", float)


# ---------- serialization (backend dataclass → script JSON shape) ----------

def serialize_action(action: FetchAction | None) -> dict | None:
    if action is None:
        return None
    out: dict[str, Any] = {"kind": action.kind}
    for key in ("form_name", "fields", "submit", "synthetic_url", "label", "page_index", "state_id"):
        value = getattr(action, key)
        if value is not None:
            out[key] = value
    return out


def serialize_context(ctx: JobContext) -> dict:
    return {
        "university_name": ctx.university_name, "agent_state": ctx.agent_state,
        "intent": ctx.intent, "parent_url": ctx.parent_url, "depth": ctx.depth,
        "org_unit_name": ctx.org_unit_name, "hints": list(ctx.hints),
    }


def serialize_job(job: FetchJob) -> dict:
    return {
        "id": job.id, "url": job.url, "status": job.status.value,
        "context": serialize_context(job.context), "created_at": job.created_at.isoformat(),
        "timeout_seconds": job.timeout_seconds, "action": serialize_action(job.action),
        "identity_url": job.identity_url,
    }


def serialize_decision(d: PendingDecision) -> dict:
    return {
        "id": d.id, "kind": d.kind, "org_unit_name": d.org_unit_name,
        "failure_count": d.failure_count, "sample_urls": list(d.sample_urls),
        "suggested_action": d.suggested_action, "status": d.status, "action": d.action,
        "created_at": d.created_at.isoformat(),
        "resolved_at": d.resolved_at.isoformat() if d.resolved_at is not None else None,
    }


def serialize_stats(stats: QueueStats) -> dict:
    return {"pending": stats.pending, "assigned": stats.assigned, "completed": stats.completed,
            "failed": stats.failed, "skipped": stats.skipped}


def serialize_frontend_health(snapshot: FrontendHealthSnapshot) -> dict:
    return {
        "alive": snapshot.alive,
        "last_seen_seconds_ago": snapshot.last_seen_seconds_ago,
        "owner_tab_id": snapshot.owner_tab_id,
        "url": snapshot.url,
        "current_job_id": snapshot.current_job_id,
        "auto_mode": snapshot.auto_mode,
        "paused": snapshot.paused,
        "client_timestamp_ms": snapshot.client_timestamp_ms,
        "stale_after_seconds": snapshot.stale_after_seconds,
    }


# ---------- deserialization (script JSON → backend dataclass) ----------

def parse_pagination_state(d: dict) -> PaginationState | None:
    try:
        synthetic_url = d["synthetic_url"]
        url = d["url"]
        if has_explicit_port(str(synthetic_url)) or has_explicit_port(str(url)):
            raise ValueError("explicit_port")
        return PaginationState(
            kind=d["kind"], state_id=d["state_id"], label=d["label"],
            page_index=int(d["page_index"]), form_name=d["form_name"], fields=dict(d["fields"]),
            submit=bool(d["submit"]), synthetic_url=synthetic_url, url=url,
            total_pages=d.get("total_pages"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("dropping malformed pagination_state %r: %r", d, exc)
        return None


def parse_pagination_states(items: object) -> list[PaginationState]:
    if not isinstance(items, list):
        return []
    parsed = (parse_pagination_state(x) for x in items if isinstance(x, dict))
    return [s for s in parsed if s is not None]


# ---------- response / request helpers ----------

def json_response(data: object, *, status: int = 200) -> web.Response:
    return web.Response(text=json.dumps(data, ensure_ascii=False), status=status,
                        content_type="application/json", charset="utf-8")


async def _read_json(request: web.Request) -> dict:
    raw = await request.read()
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _bridge(request: web.Request) -> HumanFetcherBridge:
    return request.app[BRIDGE]


def _decision_center(request: web.Request) -> DecisionCenter:
    return request.app[DECISION_CENTER]


# ---------- handlers ----------

async def handle_next_job(request: web.Request) -> web.Response:
    job = _bridge(request).next_job()
    return web.Response(status=204) if job is None else json_response(serialize_job(job))


async def handle_complete(request: web.Request) -> web.Response:
    body = await _read_json(request)
    ok = _bridge(request).complete(
        request.match_info["id"], html=str(body.get("html", "")), final_url=str(body.get("url", "")),
        title=str(body.get("title", "")), pagination_states=parse_pagination_states(body.get("pagination_states")),
    )
    return json_response({"status": "ok" if ok else "ignored", "next_job": None})


async def handle_fail(request: web.Request) -> web.Response:
    body = await _read_json(request)
    ok = _bridge(request).fail(request.match_info["id"], str(body.get("message", "")))
    return json_response({"status": "ok" if ok else "ignored"})


async def handle_skip(request: web.Request) -> web.Response:
    body = await _read_json(request)
    reason = str(body.get("reason", "")).strip()
    ok = _bridge(request).skip(request.match_info["id"], reason=reason or None)
    return json_response({"status": "ok" if ok else "ignored"})


async def handle_override(request: web.Request) -> web.Response:
    body = await _read_json(request)
    new_url = str(body.get("new_url", "")).strip()
    if not new_url:
        return web.Response(status=204)
    job = _bridge(request).override(request.match_info["id"], new_url)
    return web.Response(status=204) if job is None else json_response(serialize_job(job))


async def handle_status(request: web.Request) -> web.Response:
    bridge, dc = _bridge(request), _decision_center(request)
    current, pending = bridge.current_job(), dc.current()
    return json_response({
        "queue": serialize_stats(bridge.stats()),
        "current_job": serialize_job(current) if current is not None else None,
        "pending_decision": serialize_decision(pending) if pending is not None else None,
        "frontend_health": serialize_frontend_health(bridge.frontend_health()),
        "agent": {},
        "server_uptime_seconds": time.monotonic() - request.app[START_TIME],
    })


async def handle_heartbeat(request: web.Request) -> web.Response:
    body = await _read_json(request)
    owner_tab_id = str(body.get("owner_tab_id", "")).strip()
    if not owner_tab_id:
        return web.Response(status=400, text="owner_tab_id required")
    current_job_id = body.get("current_job_id")
    client_timestamp_ms = body.get("timestamp")
    _bridge(request).record_frontend_heartbeat(
        owner_tab_id=owner_tab_id,
        url=str(body.get("url", "")),
        current_job_id=str(current_job_id) if current_job_id is not None else None,
        auto_mode=bool(body.get("auto_mode", False)),
        paused=bool(body.get("paused", False)),
        client_timestamp_ms=float(client_timestamp_ms) if isinstance(client_timestamp_ms, (int, float)) else None,
    )
    return json_response({"status": "ok"})


async def handle_decision(request: web.Request) -> web.Response:
    decision = _decision_center(request).current()
    return web.Response(status=204) if decision is None else json_response(serialize_decision(decision))


async def handle_resolve_decision(request: web.Request) -> web.Response:
    body = await _read_json(request)
    ok = await _decision_center(request).resolve(request.match_info["id"], str(body.get("action", "")).strip())
    return json_response({"status": "ok" if ok else "ignored"})


# ---------- app wiring ----------

def create_app(bridge: HumanFetcherBridge, decision_center: DecisionCenter) -> web.Application:
    app = web.Application()
    app[BRIDGE] = bridge
    app[DECISION_CENTER] = decision_center
    app[START_TIME] = time.monotonic()
    app.add_routes([
        web.get(f"{API_PREFIX}/jobs/next", handle_next_job),
        web.post(f"{API_PREFIX}/jobs/{{id}}/complete", handle_complete),
        web.post(f"{API_PREFIX}/jobs/{{id}}/fail", handle_fail),
        web.post(f"{API_PREFIX}/jobs/{{id}}/skip", handle_skip),
        web.post(f"{API_PREFIX}/jobs/{{id}}/override", handle_override),
        web.get(f"{API_PREFIX}/status", handle_status),
        web.post(f"{API_PREFIX}/heartbeat", handle_heartbeat),
        web.get(f"{API_PREFIX}/decision", handle_decision),
        web.post(f"{API_PREFIX}/decision/{{id}}/resolve", handle_resolve_decision),
    ])
    return app


async def run_server(app: web.Application, host: str, port: int) -> web.AppRunner:
    """Start the server in the running loop and return the runner for SP7 lifecycle
    management (await runner.cleanup() to stop)."""
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    logger.info("dext fetch bridge listening on http://%s:%d%s", host, port, API_PREFIX)
    return runner
