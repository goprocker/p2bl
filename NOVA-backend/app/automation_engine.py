"""Deterministic rule agents for context-aware smart-home decisions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

PRIORITY = {"default": 0, "energy": 100, "comfort": 200,
            "accessibility": 300, "resident": 400, "safety": 500}

RULES = (
    {"id": "evening-arrival-light", "name": "Evening arrival lighting",
     "group": "comfort", "priority": "comfort",
     "explanation": "Occupied room, low ambient light, and evening time require lighting."},
    {"id": "away-high-load", "name": "Away-mode energy reduction",
     "group": "energy", "priority": "energy",
     "explanation": "Empty home and high demand allow approved standby loads to switch off."},
    {"id": "unusual-entry", "name": "Unexpected entry protection",
     "group": "safety", "priority": "safety",
     "explanation": "Door opened in away mode without authorization requires an alert."},
)


def rule_catalog(enabled: dict[str, bool] | None = None) -> list[dict[str, Any]]:
    states = enabled or {}
    return [{**rule, "enabled": states.get(rule["id"], True)} for rule in RULES]


def evaluate(payload: dict[str, Any], enabled: dict[str, bool] | None = None,
             active_overrides: list[dict[str, Any]] | None = None,
             now: datetime | None = None) -> dict[str, Any]:
    """Run sensing, context, policy and conflict agents without side effects."""
    current = now or datetime.now(timezone.utc)
    observed_at = payload.get("observed_at") or current
    if isinstance(observed_at, str):
        observed_at = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    observations = payload.get("observations", {})
    room = payload.get("room", "living_room")
    trace = [{"agent": "Sensing Agent",
              "message": f"Accepted {len(observations)} observations from {room}."}]

    facts: dict[str, Any] = {}
    fact_rows: list[dict[str, Any]] = []
    consent = payload.get("occupancy_automation", True)
    for name, value in observations.items():
        if value is None or (name == "occupied" and not consent):
            continue
        ttl = 1800 if name == "away_mode" else 300 if name in {"occupied", "light_level_lux"} else 120
        valid_until = observed_at + timedelta(seconds=ttl)
        if valid_until <= current:
            continue
        facts[name] = value
        fact_rows.append({"name": name, "value": value, "source": "simulator",
                          "observed_at": observed_at.isoformat(),
                          "valid_until": valid_until.isoformat()})
    context_message = ("Occupancy automation paused by resident; occupancy fact omitted."
                       if not consent else
                       f"Published {len(fact_rows)} current facts; stale facts were discarded.")
    trace.append({"agent": "Context Agent", "message": context_message})

    candidates: list[dict[str, Any]] = []
    matched: list[dict[str, Any]] = []
    states = enabled or {}

    def match(rule_id: str, actions: list[dict[str, Any]]) -> None:
        rule = next(item for item in RULES if item["id"] == rule_id)
        matched.append({**rule, "facts": [row["name"] for row in fact_rows]})
        for action in actions:
            candidates.append({**action, "rule_id": rule_id,
                               "priority": rule["priority"],
                               "priority_value": PRIORITY[rule["priority"]],
                               "explanation": rule["explanation"]})

    unusual = (facts.get("door_open") is True and facts.get("away_mode") is True
               and facts.get("authorized_entry") is not True)
    if states.get("unusual-entry", True) and unusual:
        match("unusual-entry", [{"kind": "notification", "target": "resident",
                                  "command": "alert", "value": "Unexpected entry detected"}])

    hour = facts.get("local_hour", observed_at.hour)
    evening = (facts.get("occupied") is True
               and isinstance(facts.get("light_level_lux"), (int, float))
               and facts["light_level_lux"] < 100 and 18 <= hour < 23)
    if states.get("evening-arrival-light", True) and evening:
        match("evening-arrival-light", [{"kind": "device", "target": "living room light",
                                          "command": "set_state", "value": "on"}])

    approved = payload.get("approved_standby_devices", [])
    energy = (facts.get("occupied") is False and facts.get("away_mode") is True
              and isinstance(facts.get("home_power_w"), (int, float))
              and facts["home_power_w"] >= 1000 and bool(approved))
    if states.get("away-high-load", True) and energy:
        match("away-high-load", [{"kind": "device", "target": device,
                                   "command": "set_state", "value": "off"}
                                  for device in approved])

    overrides = list(active_overrides or [])
    command = payload.get("resident_command")
    if command:
        overrides.append({**command, "room": room,
                          "expires_at": current + timedelta(minutes=command.get("hold_minutes", 30))})
    for override in overrides:
        expires_at = override["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)
        if expires_at <= current or override.get("room") != room:
            continue
        candidates.append({"kind": "device", "target": override["device"],
                           "command": "set_state", "value": override["state"],
                           "rule_id": "resident-override", "priority": "resident",
                           "priority_value": PRIORITY["resident"],
                           "explanation": f"Resident override active until {expires_at.isoformat()}."})

    trace.append({"agent": "Rule Agent",
                  "message": f"Matched {len(matched)} policies; proposed {len(candidates)} actions."})
    if energy:
        trace.append({"agent": "Energy Agent",
                      "message": f"Limited actions to {len(approved)} approved standby loads."})

    selected: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    safety_lock = any(action["priority"] == "safety" for action in candidates)
    occupied_targets: set[tuple[str, str]] = set()
    for action in sorted(candidates, key=lambda item: item["priority_value"], reverse=True):
        key = (action["kind"], action["target"].lower())
        if safety_lock and action["priority"] not in {"safety", "resident"}:
            rejected.append({**action, "rejection_reason": "Suppressed by active safety policy."})
        elif key in occupied_targets:
            rejected.append({**action, "rejection_reason": "Higher-priority action controls this target."})
        else:
            occupied_targets.add(key)
            selected.append(action)
    trace.append({"agent": "Conflict Resolver",
                  "message": f"Selected {len(selected)} actions; rejected {len(rejected)} conflicts."})
    return {"room": room, "observed_at": observed_at.isoformat(), "facts": fact_rows,
            "matched_rules": matched, "selected_actions": selected,
            "rejected_actions": rejected, "trace": trace,
            "new_overrides": [item for item in overrides if item not in (active_overrides or [])]}
