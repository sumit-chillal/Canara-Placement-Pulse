"""
Firebase Admin — lazy singleton. Used by ingest to publish topic
messages, and by /api/subscribe + /api/unsubscribe to bind/unbind
incoming FCM tokens to the `placement-updates` topic.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, messaging

logger = logging.getLogger("placement-pulse.fcm")

_APP: firebase_admin.App | None = None

# This file's own directory (backend/), used to resolve a relative
# FIREBASE_SERVICE_ACCOUNT_PATH regardless of where the project is
# checked out or what the process's working directory happens to be.
_BACKEND_DIR = Path(__file__).resolve().parent


def _resolve_cred_path(raw: str) -> Path:
    p = Path(raw).expanduser()
    return p if p.is_absolute() else (_BACKEND_DIR / p)


def _init() -> firebase_admin.App:
    global _APP
    if _APP is not None:
        return _APP
    if firebase_admin._apps:
        _APP = firebase_admin.get_app()
        return _APP
    cred_path_raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT_PATH", "")
    cred_path = _resolve_cred_path(cred_path_raw) if cred_path_raw else None
    if not cred_path or not cred_path.exists():
        raise RuntimeError(
            f"Firebase service account file missing. "
            f"FIREBASE_SERVICE_ACCOUNT_PATH={cred_path_raw!r} "
            f"resolved to {cred_path} which does not exist. "
            f"Set it to an absolute path, or a path relative to backend/ "
            f"(e.g. 'secrets/firebase-admin.json')."
        )
    cred = credentials.Certificate(str(cred_path))
    _APP = firebase_admin.initialize_app(cred)
    logger.info("Firebase Admin SDK initialised for project %s", _APP.project_id)
    return _APP


def publish_to_topic(
    topic: str | None = None,
    *,
    title: str,
    body: str,
    data: dict,
    condition: str | None = None,
) -> str:
    """
    Publish one message. Returns the FCM message ID. Raises on error.

    Deliberately DATA-ONLY (no top-level `notification` field). A
    message with a `notification` payload gets auto-displayed by the
    browser/OS AND can still separately trigger the service worker's
    manual showNotification() call, producing two notifications for
    one message. Sending everything as `data` means only the SW's
    onBackgroundMessage handler ever displays anything — exactly one
    notification, with full control over icon/badge/click behavior.

    Pass either `topic` (plain topic name) or `condition` (an FCM
    condition string like "'x-branch-CSE' in topics || 'x-branch-ISE'
    in topics"), never both. `condition` is what makes branch-specific
    targeting safe for a student subscribed to more than one branch —
    a single condition-matched send reaches a device exactly once,
    whereas publishing the same message separately to each matching
    branch topic would deliver it once per topic the device happens to
    be subscribed to.
    """
    _init()
    # FCM data payload must be all-strings.
    string_data = {k: str(v) for k, v in data.items() if v is not None}
    string_data["title"] = title
    string_data["body"] = body or ""

    target = {"condition": condition} if condition else {"topic": topic}
    msg = messaging.Message(data=string_data, **target)
    return messaging.send(msg)


def subscribe_token_to_topic(token: str, topic: str) -> dict:
    """Bind one FCM token to `topic`. Fire-and-forget — we don't persist it."""
    _init()
    resp = messaging.subscribe_to_topic([token], topic)
    errors = [
        {"index": e.index, "reason": e.reason} for e in (resp.errors or [])
    ]
    return {
        "success_count": resp.success_count,
        "failure_count": resp.failure_count,
        "errors": errors,
    }


def unsubscribe_token_from_topic(token: str, topic: str) -> dict:
    """
    Unbind one FCM token from `topic` (used by the bell button's "turn
    off" path). Mirrors subscribe_token_to_topic. Fire-and-forget — same
    as subscribe, we don't persist tokens either direction.
    """
    _init()
    resp = messaging.unsubscribe_from_topic([token], topic)
    errors = [
        {"index": e.index, "reason": e.reason} for e in (resp.errors or [])
    ]
    return {
        "success_count": resp.success_count,
        "failure_count": resp.failure_count,
        "errors": errors,
    }