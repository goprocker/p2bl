"""Smart-room control — bridges NOVA to the ProjectMyRoom API.

ProjectMyRoom (Express + MongoDB + Socket.IO, ../ProjectMyRoom) manages the
real room devices: lights, fans, blinds, sensors. Its device state lives in
the same local MongoDB instance NOVA uses (database `project_my_room`).

control_device(device, state)  - turn a device on/off by name or type
list_room_devices()            - what devices exist and their current state

Config in .env: MYROOM_API_URL, MYROOM_API_TOKEN.
"""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_ON_WORDS = {"on", "true", "1", "enable", "enabled", "yes"}
_ALL_WORDS = {"all", "everything", "all devices", "every device", "all the devices"}


def _headers() -> dict:
    return {"x-auth-token": settings.myroom_api_token}


async def _list_devices(client: httpx.AsyncClient) -> list[dict]:
    resp = await client.get(f"{settings.myroom_api_url}/api/listDevice", headers=_headers())
    resp.raise_for_status()
    return resp.json().get("data", [])


def _find(devices: list[dict], query: str) -> dict | None:
    """Match by name first ('bedroom light'), then by type ('light')."""
    q = query.strip().lower()
    by_name = next((d for d in devices if q in d["deviceName"].lower()), None)
    if by_name:
        return by_name
    return next(
        (d for d in devices if d["deviceType"].lower() in q or q in d["deviceType"].lower()),
        None,
    )


async def _set_device(client: httpx.AsyncClient, device: dict, want_on: bool) -> str:
    """Set one device's state and report the REAL outcome.

    The ProjectMyRoom API answers HTTP 200 even on failure ('Device not
    found', ...), so the JSON `status` field is the only truth.
    """
    resp = await client.post(
        f"{settings.myroom_api_url}/api/changeDeviceStatus",
        headers=_headers(),
        json={"deviceID": device["deviceID"], "deviceStatus": want_on},
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("status") != "success":
        logger.warning("Set %s failed: %s", device["deviceName"], body.get("message"))
        return f"{device['deviceName']}: FAILED — {body.get('message', 'unknown error')}"
    logger.info("Set %s (%s) -> %s", device["deviceName"], device["deviceID"], want_on)
    return f"{device['deviceName']}: now {'ON' if want_on else 'OFF'}"


async def control_device(device: str, state: str) -> str:
    """Turn one room device — or 'all' of them — on/off through the ProjectMyRoom API."""
    if not settings.myroom_api_token:
        return "Smart-room control is not configured — set MYROOM_API_TOKEN in backend/.env."

    want_on = str(state).strip().lower() in _ON_WORDS

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            devices = await _list_devices(client)
            if not devices:
                return "No smart-room devices are registered in ProjectMyRoom yet."

            if device.strip().lower() in _ALL_WORDS:
                targets = devices
            else:
                match = _find(devices, device)
                if match is None:
                    available = ", ".join(f"{d['deviceName']} ({d['deviceType']})" for d in devices)
                    return f"No device matches '{device}'. Available devices: {available}"
                targets = [match]

            results = [await _set_device(client, d, want_on) for d in targets]
    except httpx.ConnectError:
        return (
            "The smart-room server is not reachable — start it with `npm start` "
            "in the ProjectMyRoom folder."
        )

    if len(results) == 1:
        return f"Done — {results[0]}."
    return "Done:\n" + "\n".join(f"- {r}" for r in results)


async def list_room_devices() -> str:
    """Human-readable list of room devices with their current on/off state."""
    if not settings.myroom_api_token:
        return "Smart-room control is not configured — set MYROOM_API_TOKEN in backend/.env."
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            devices = await _list_devices(client)
    except httpx.ConnectError:
        return (
            "The smart-room server is not reachable — start it with `npm start` "
            "in the ProjectMyRoom folder."
        )
    if not devices:
        return "No smart-room devices are registered in ProjectMyRoom yet."
    lines = ["Room devices:"]
    for d in devices:
        state = "ON" if d.get("deviceStatus") else "OFF"
        lines.append(f"- {d['deviceName']} ({d['deviceType']}, {d['deviceID']}): {state}")
    return "\n".join(lines)
