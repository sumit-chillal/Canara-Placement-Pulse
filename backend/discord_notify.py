"""
discord_notify.py — best-effort Discord webhook alerts for backend
health events (Mongo connectivity, ingest failures, startup issues).

The webhook URL is read from the DISCORD_WEBHOOK_URL environment
variable — never hardcoded here. If it's unset, every call in this
module is a silent no-op, so nothing breaks for anyone who hasn't
configured a webhook yet.

Scope note: this module can only alert on what the BACKEND can
observe — Mongo connectivity and ingest-run failures. It cannot detect
the frontend (Firebase Hosting) being down, since a dead static site
has no backend code running to report it. Pair this with an external
uptime monitor (e.g. UptimeRobot) pointed at the frontend URL for that
piece — see the "potential improvements" notes for detail.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import httpx

logger = logging.getLogger("placement-pulse.discord")

_COLORS = {
    "error": 0xE05252,    # red
    "warning": 0xC68A2F,  # amber
    "info": 0x7A9A3A,     # moss green
}


def notify_discord(
    title: str,
    message: str,
    *,
    severity: str = "error",
    fields: dict | None = None,
) -> None:
    """
    Best-effort Discord webhook alert. Never raises — a monitoring
    call failing must never take down the thing it's monitoring.
    No-ops silently if DISCORD_WEBHOOK_URL isn't set in the
    environment.
    """
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return

    embed = {
        "title": title,
        "description": message,
        "color": _COLORS.get(severity, _COLORS["error"]),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "Placement Pulse · backend monitor"},
    }
    if fields:
        embed["fields"] = [
            {"name": str(k), "value": str(v), "inline": True}
            for k, v in fields.items()
        ]

    try:
        with httpx.Client(timeout=5.0) as client:
            client.post(webhook_url, json={"embeds": [embed]})
    except Exception:  # noqa: BLE001 — monitoring must never break the app
        logger.exception("Discord webhook notification failed")