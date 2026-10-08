"""Coordinate rule evaluation, audit history, and optional real actuation."""

from collections import deque
from datetime import datetime, timezone
from time import monotonic
from typing import Any
from uuid import uuid4

from app import automation_engine
from app.config import settings
from app.db import mongo
from app.tools import smartroom_tool

_rule_states: dict[str, bool] = {}
_overrides: list[dict[str, Any]] = []
_recent: deque[dict[str, Any]] = deque(maxlen=100)


async def rules() -> list[dict[str, Any]]:
    try:
        _rule_states.update(await mongo.get_automation_rule_states())
    except Exception:
        pass
    return automation_engine.rule_catalog(_rule_states)


async def set_rule_enabled(rule_id: str, enabled: bool) -> dict[str, Any] | None:
    if rule_id not in {item["id"] for item in automation_engine.RULES}:
        return None
    _rule_states[rule_id] = enabled
    try:
        await mongo.set_automation_rule_state(rule_id, enabled)
    except Exception:
        pass
    return next(item for item in automation_engine.rule_catalog(_rule_states)
                if item["id"] == rule_id)


async def run(payload: dict[str, Any]) -> dict[str, Any]:
    started = monotonic()
    now = datetime.now(timezone.utc)
    _overrides[:] = [item for item in _overrides if item["expires_at"] > now]
    decision = automation_engine.evaluate(payload, _rule_states, _overrides, now)
    _overrides.extend(decision.pop("new_overrides"))
    dry_run = payload.get("dry_run", True)
    execution = []
    for action in decision["selected_actions"]:
        if action["kind"] == "notification":
            execution.append({**action, "status": "notified", "result": action["value"]})
        elif dry_run:
            execution.append({**action, "status": "proposed",
                              "result": "Dry-run: no device command sent."})
        elif not settings.automation_live_enabled:
            execution.append({**action, "status": "blocked",
                              "result": "Live automation disabled by server configuration."})
        else:
            result = await smartroom_tool.control_device(action["target"], action["value"])
            execution.append({**action, "status": "executed" if result.startswith("Done") else "failed",
                              "result": result})
    decision["trace"].extend([
        {"agent": "Device Agent",
         "message": "Recorded proposed commands." if dry_run else "Processed selected commands."},
        {"agent": "Notification Agent",
         "message": "Published decisions, explanations, and execution alerts."},
    ])
    decision.update({"id": str(uuid4()), "dry_run": dry_run, "execution": execution,
                     "duration_ms": int((monotonic() - started) * 1000),
                     "created_at": now.isoformat()})
    _recent.appendleft(decision)
    try:
        await mongo.save_automation_decision(decision)
    except Exception:
        pass
    return decision


async def decisions(limit: int = 20) -> list[dict[str, Any]]:
    try:
        stored = await mongo.list_automation_decisions(limit)
        if stored:
            return stored
    except Exception:
        pass
    return list(_recent)[:limit]
