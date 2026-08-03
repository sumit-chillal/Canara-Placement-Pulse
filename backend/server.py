"""
Placement Pulse — Backend (FastAPI).

Phase 0: /api/health
Phase 1: /api/admin/ingest/* + IST-aware APScheduler
Phase 2: /api/entries, /api/entries/{slno}  (public read API)
Phase 7: /api/files/{slno}/{index}          (college-file proxy)
"""
from fastapi import FastAPI, APIRouter, HTTPException, Query, Request, Header, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from pathlib import Path
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo
import logging
import os
import re

import httpx

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.combining import OrTrigger

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from pydantic import BaseModel, Field

from ingest import run_ingest, build_notification, normalize_entry, _enforce_notice_cap
from firebase_client import (
    publish_to_topic,
    subscribe_token_to_topic,
    unsubscribe_token_from_topic,
)

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

# --- Config ------------------------------------------------------------
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
CORS_ORIGINS = [o.strip() for o in os.environ["CORS_ORIGINS"].split(",") if o.strip()]
COLLEGE_API_URL = os.environ["COLLEGE_API_URL"]
FCM_TOPIC = os.environ["FCM_TOPIC"]
FIREBASE_PROJECT_ID = os.environ["FIREBASE_PROJECT_ID"]
# Phase 6 · admin auth. If unset, all /api/admin/* routes 503. If set,
# callers must present matching `X-Admin-Token` header.
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()

IST = ZoneInfo("Asia/Kolkata")

# --- Logging -----------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("placement-pulse")

# --- Mongo -------------------------------------------------------------
mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client[DB_NAME]

# --- Rate limiter ------------------------------------------------------
# Public endpoints are unauthenticated; throttle per client IP.
limiter = Limiter(key_func=get_remote_address, default_limits=["120/minute"])

# --- Scheduler ---------------------------------------------------------
# Day (IST 08:00–23:30): every 30 min. Night (IST 00:00–06:00): every 2 h.
_day_trigger = CronTrigger(hour="8-23", minute="0,30", timezone=IST)
_night_trigger = CronTrigger(hour="0,2,4,6", minute="0", timezone=IST)
scheduler = AsyncIOScheduler(timezone=IST)


async def _scheduled_ingest():
    logger.info("Scheduled ingest tick (IST %s)", datetime.now(IST).isoformat())
    await run_ingest(db, COLLEGE_API_URL, notify=True, notify_topic=FCM_TOPIC)


# --- App ---------------------------------------------------------------
app = FastAPI(title="Placement Pulse API", version="0.5.0-notif-toggle")
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(_request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limited",
            "detail": f"Too many requests. Limit: {exc.detail}",
        },
    )


@app.exception_handler(RequestValidationError)
async def _validation_handler(_request: Request, exc: RequestValidationError):
    """Return a clean 400 (not 422) for malformed query/path params."""
    errors = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", []) if p not in ("body",))
        errors.append({"field": loc, "message": err.get("msg"), "type": err.get("type")})
    return JSONResponse(
        status_code=400,
        content={"error": "bad_request", "errors": errors},
    )


api_router = APIRouter(prefix="/api")

# --- Query validation --------------------------------------------------
# ISO week: YYYY-Www where ww in 01..53
WEEK_REGEX = r"^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$"

SortMode = Literal["latest", "company", "closingDate"]


def _sort_spec(sort: SortMode) -> list[tuple[str, int]]:
    """Map the public sort enum to a MongoDB sort spec."""
    if sort == "company":
        # Empty company strings sink to the bottom naturally in asc sort of
        # non-nulls; then within company, newest first.
        return [("company", 1), ("date", -1), ("slno", -1)]
    if sort == "closingDate":
        # We don't extract a real deadline yet; the closest honest proxy is
        # date ascending (earliest posted first ≈ soonest to close).
        return [("date", 1), ("slno", 1)]
    # "latest" — newest week first, then newest slno as tiebreaker.
    return [("date", -1), ("slno", -1)]


def _serialize_links(slno: int, links: list[dict] | None) -> list[dict]:
    """
    Turn internally-stored link records ({label, sourceUrl,
    hostedByCollege}) into the public {label, url} shape.

    hostedByCollege=True  → url = our own proxy route (/api/files/...),
                             so the browser only ever talks to us.
    hostedByCollege=False → url = the external sourceUrl unchanged
                             (a company application form, Drive link,
                             etc. — not the college's own site).
    """
    out = []
    for i, link in enumerate(links or []):
        if link.get("hostedByCollege"):
            url = f"/api/files/{slno}/{i}"
        else:
            url = link.get("sourceUrl", "")
        out.append({"label": link.get("label"), "url": url})
    return out


def _serialize(doc: dict) -> dict:
    """Strip Mongo internals, translate link records, ensure JSON-safe values."""
    doc.pop("_id", None)
    doc["registrationLinks"] = _serialize_links(
        doc.get("slno"), doc.get("registrationLinks")
    )
    return doc


# --- Admin auth guard (Phase 6) --------------------------------------
async def require_admin_token(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """
    Applied to every /api/admin/* route. If ADMIN_TOKEN is not set in
    the environment, admin surface is disabled entirely (503) — safer
    default than silently open.
    """
    if not ADMIN_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="admin_disabled: server has no ADMIN_TOKEN configured",
        )
    if not x_admin_token or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(
            status_code=401,
            detail="unauthorized: missing or invalid X-Admin-Token header",
        )
    return True


# --- Public endpoints --------------------------------------------------
@api_router.get("/")
async def root():
    return {
        "name": "Placement Pulse API",
        "version": app.version,
        "status": "ok",
    }


@api_router.get("/health")
async def health():
    """Liveness + last-poll snapshot for our own monitoring."""
    mongo_ok = False
    mongo_error = None
    try:
        await mongo_client.admin.command("ping")
        mongo_ok = True
    except Exception as exc:  # noqa: BLE001
        mongo_error = str(exc)

    last_run = None
    if mongo_ok:
        last_run = await db.ingest_runs.find_one(
            {}, {"_id": 0}, sort=[("started_at", -1)]
        )

    total_notices = None
    if mongo_ok:
        total_notices = await db.notices.count_documents({})

    ist_now = datetime.now(IST)
    return {
        "status": "ok" if mongo_ok else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ist_time": ist_now.isoformat(),
        "ist_hour": ist_now.hour,
        "scheduler_running": scheduler.running,
        "total_notices": total_notices,
        "last_poll": last_run,
        "checks": {
            "mongo": {"ok": mongo_ok, "error": mongo_error},
            "college_api_url": COLLEGE_API_URL,
            "fcm_topic": FCM_TOPIC,
            "firebase_project_id": FIREBASE_PROJECT_ID,
        },
    }


@api_router.get("/entries")
@limiter.limit("60/minute")
async def list_entries(
    request: Request,
    week: str | None = Query(
        None,
        regex=WEEK_REGEX,
        description="ISO week filter, e.g. '2026-W29'. Rejected if malformed.",
    ),
    q: str | None = Query(
        None,
        min_length=1,
        max_length=100,
        description="Case-insensitive substring search across headline1 + company.",
    ),
    company: str | None = Query(
        None,
        min_length=1,
        max_length=100,
        description="Case-insensitive substring match on the company field.",
    ),
    hasRegistration: bool | None = Query(
        None,
        alias="hasRegistration",
        description="If true, only notices with at least one registrationLink. "
                    "If false, only notices without any. Omit for both.",
    ),
    noticeType: str | None = Query(
        None,
        regex=r"^(drive|internship|results|ppt|notice)$",
        description="Filter by extracted notice type.",
    ),
    sort: SortMode = Query(
        "latest",
        description="Sort order: 'latest' (default), 'company', 'closingDate'.",
    ),
    limit: int = Query(100, ge=1, le=200),
    cursor: int | None = Query(
        None,
        ge=1,
        description="Offset by slno (exclusive). Use the last returned slno "
                    "to page through the list.",
    ),
):
    """
    Public list endpoint. No auth, rate-limited to 60/minute per IP.
    Phase 6.5 additions: q / company / hasRegistration / noticeType. All
    filters compose with each other; each is optional.
    """
    query: dict = {}
    if week is not None:
        query["isoWeek"] = week
    if cursor is not None:
        # Cursor semantics depend on sort direction; for 'latest' (slno desc)
        # we return entries strictly older than the cursor.
        query["slno"] = {"$lt": cursor} if sort == "latest" else {"$gt": cursor}
    if company is not None:
        # Escape regex metachars — user input, safe.
        query["company"] = {"$regex": re.escape(company), "$options": "i"}
    if q is not None:
        # Full-text-ish: match either headline1 or company (case-insensitive).
        needle = re.escape(q)
        query["$or"] = [
            {"headline1": {"$regex": needle, "$options": "i"}},
            {"company":   {"$regex": needle, "$options": "i"}},
            {"messageExcerpt": {"$regex": needle, "$options": "i"}},
        ]
    if hasRegistration is True:
        query["registrationLinks.0"] = {"$exists": True}
    elif hasRegistration is False:
        query["registrationLinks"] = {"$size": 0}
    if noticeType is not None:
        query["noticeType"] = noticeType

    sort_spec = _sort_spec(sort)
    docs = await (
        db.notices.find(query, {"_id": 0, "detailsHtml": 0})
        .sort(sort_spec)
        .limit(limit)
        .to_list(length=limit)
    )

    next_cursor = docs[-1]["slno"] if len(docs) == limit else None
    return {
        "entries": [_serialize(d) for d in docs],
        "count": len(docs),
        "sort": sort,
        "week": week,
        "next_cursor": next_cursor,
    }


@api_router.get("/entries/{slno}")
@limiter.limit("120/minute")
async def get_entry(request: Request, slno: int):
    """
    Full stored data for a single notice. `detailsHtml` is returned RAW —
    the frontend MUST sanitize with DOMPurify before rendering.
    """
    if slno < 1:
        raise HTTPException(status_code=400, detail="slno must be a positive integer")

    doc = await db.notices.find_one({"slno": slno}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail=f"Notice slno={slno} not found")
    return _serialize(doc)


# --- Phase 7 · college file proxy --------------------------------------
@api_router.get("/files/{slno}/{index}")
@limiter.limit("60/minute")
async def get_file(request: Request, slno: int, index: int):
    """
    Streams a college-hosted attachment (JD, eligibility list, etc.)
    through OUR backend so the browser never talks to
    canaraengineering.in directly (Architecture Rule #2).

    Only serves links stored with hostedByCollege=True. External
    application links (other domains) are served directly to the
    frontend as-is and never routed through here — see
    ingest.py::_resolve_link for the split.
    """
    doc = await db.notices.find_one(
        {"slno": slno}, {"_id": 0, "registrationLinks": 1, "headline1": 1}
    )
    if not doc:
        raise HTTPException(status_code=404, detail=f"Notice slno={slno} not found")

    links = doc.get("registrationLinks") or []
    if index < 0 or index >= len(links):
        raise HTTPException(
            status_code=404,
            detail=f"No attachment at index={index} for slno={slno}",
        )

    link = links[index]
    if not link.get("hostedByCollege"):
        raise HTTPException(
            status_code=400,
            detail="This link is external and cannot be proxied",
        )

    source_url = link.get("sourceUrl")
    if not source_url:
        raise HTTPException(
            status_code=502, detail="No source URL stored for this attachment"
        )

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            upstream = await client.get(source_url)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream fetch failed: {exc}")

    if upstream.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Upstream returned {upstream.status_code} for this attachment",
        )

    content_type = upstream.headers.get("content-type", "application/octet-stream")
    filename = source_url.rstrip("/").split("/")[-1] or f"attachment-{slno}-{index}"

    return StreamingResponse(
        iter([upstream.content]),
        media_type=content_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# --- Admin endpoints (Phase 1 + Phase 3 + Phase 6) --------------------
@api_router.post("/admin/ingest/run", dependencies=[Depends(require_admin_token)])
async def trigger_ingest(notify: bool = Query(True)):
    """
    Manual poll trigger. Pass ?notify=false for a silent backfill run
    (no FCM pushes fanned out).
    """
    return await run_ingest(db, COLLEGE_API_URL, notify=notify, notify_topic=FCM_TOPIC)


@api_router.get("/admin/ingest/runs", dependencies=[Depends(require_admin_token)])
async def list_ingest_runs(limit: int = Query(20, ge=1, le=200)):
    cursor = (
        db.ingest_runs.find({}, {"_id": 0})
        .sort("started_at", -1)
        .limit(limit)
    )
    return {"runs": await cursor.to_list(length=limit)}


@api_router.get("/admin/notices/count", dependencies=[Depends(require_admin_token)])
async def notices_count():
    total = await db.notices.count_documents({})
    latest_cursor = (
        db.notices.find({}, {"_id": 0, "slno": 1, "headline1": 1, "date": 1})
        .sort([("date", -1), ("slno", -1)])
        .limit(5)
    )
    return {"total": total, "latest": await latest_cursor.to_list(length=5)}


@api_router.post("/admin/notices/trim", dependencies=[Depends(require_admin_token)])
async def admin_trim_notices(limit: int = Query(100, ge=1, le=5000)):
    """
    One-shot: immediately trim the notices collection down to `limit`
    (default 100), deleting the oldest overflow first. This is the same
    logic run_ingest() now applies automatically at the end of every
    poll — this endpoint exists for an immediate cleanup (e.g. bringing
    an existing larger collection down to the new cap right away)
    without waiting for the next scheduled run.
    """
    deleted = await _enforce_notice_cap(db, limit)
    remaining = await db.notices.count_documents({})
    return {"deleted": deleted, "remaining": remaining, "limit": limit}


# --- Phase 3 · Subscribe / notify -------------------------------------
# Same branch codes the frontend filter bar and ingest extractor already
# use — kept as a fixed allowlist here too so a client can't subscribe
# a token to an arbitrary/unbounded set of FCM topics.
VALID_BRANCH_CODES = {
    "CSE", "ISE", "IT", "ECE", "EEE", "ME", "CV", "AIML", "DS", "MBA", "MCA",
}


class SubscribeBody(BaseModel):
    token: str = Field(min_length=20, max_length=4096)
    # Optional: which branch(es) this device wants notices for, beyond
    # the always-on "ALL branches" topic every subscriber gets. Empty
    # list = ALL-branches notices only (the pre-existing behavior).
    branches: list[str] = Field(default_factory=list)

    def branch_topics(self) -> list[str]:
        return [
            f"{FCM_TOPIC}-branch-{b}"
            for b in self.branches
            if b in VALID_BRANCH_CODES
        ]


@api_router.post("/subscribe")
@limiter.limit("30/minute")
async def subscribe(request: Request, body: SubscribeBody):
    """
    Bind a device FCM token to the base `placement-updates` topic
    (every subscriber gets ALL-branches notices regardless of branch
    preference) plus one per-branch topic for each branch the device
    selected, so a CSE student stops receiving Mechanical-only drives.
    We do NOT persist the token — FCM handles fan-out on its side.
    """
    topics = [FCM_TOPIC, *body.branch_topics()]
    results = {}
    any_failure = False
    for topic in topics:
        try:
            results[topic] = subscribe_token_to_topic(body.token, topic)
            if results[topic]["failure_count"] > 0:
                any_failure = True
        except Exception as exc:  # noqa: BLE001
            logger.exception("FCM subscribe failed for topic=%s", topic)
            results[topic] = {"error": f"{type(exc).__name__}: {exc}"}
            any_failure = True
    return {
        "subscribed": not any_failure,
        "topics": topics,
        "results": results,
    }


@api_router.post("/unsubscribe")
@limiter.limit("30/minute")
async def unsubscribe(request: Request, body: SubscribeBody):
    """
    Unbind a device FCM token from the base topic plus whichever
    branch topics it's currently on — the client sends back the same
    `branches` list it originally subscribed with. Mirrors /subscribe.
    Best-effort from the client's point of view: the frontend also
    deletes its local FCM token regardless of this call's outcome,
    since that's what actually guarantees this device stops receiving
    pushes (a deleted token can't be delivered to, independent of
    topic membership).
    """
    topics = [FCM_TOPIC, *body.branch_topics()]
    results = {}
    any_failure = False
    for topic in topics:
        try:
            results[topic] = unsubscribe_token_from_topic(body.token, topic)
            if results[topic]["failure_count"] > 0:
                any_failure = True
        except Exception as exc:  # noqa: BLE001
            logger.exception("FCM unsubscribe failed for topic=%s", topic)
            results[topic] = {"error": f"{type(exc).__name__}: {exc}"}
            any_failure = True
    return {
        "unsubscribed": not any_failure,
        "topics": topics,
        "results": results,
    }


@api_router.post("/subscribe/branches")
@limiter.limit("30/minute")
async def update_branches(request: Request, body: SubscribeBody):
    """
    Reconciles a device's branch-topic subscriptions to EXACTLY
    `body.branches` — subscribes to newly-added ones, unsubscribes
    from ones no longer wanted, always leaves the base (ALL-branches)
    topic alone since every device stays subscribed to that
    regardless. This is what lets the notification bell's "edit
    branches" action change preference in place: no full
    unsubscribe+resubscribe cycle, and critically no local
    deleteToken() call, so the device's FCM token never rotates.
    """
    desired = {b for b in body.branches if b in VALID_BRANCH_CODES}
    to_subscribe = [FCM_TOPIC] + [f"{FCM_TOPIC}-branch-{b}" for b in desired]
    to_unsubscribe = [
        f"{FCM_TOPIC}-branch-{b}" for b in (VALID_BRANCH_CODES - desired)
    ]

    results = {}
    any_failure = False
    for topic in to_subscribe:
        try:
            results[topic] = subscribe_token_to_topic(body.token, topic)
            if results[topic]["failure_count"] > 0:
                any_failure = True
        except Exception as exc:  # noqa: BLE001
            logger.exception("Branch reconcile subscribe failed for topic=%s", topic)
            results[topic] = {"error": f"{type(exc).__name__}: {exc}"}
            any_failure = True
    for topic in to_unsubscribe:
        try:
            results[topic] = unsubscribe_token_from_topic(body.token, topic)
            if results[topic]["failure_count"] > 0:
                any_failure = True
        except Exception as exc:  # noqa: BLE001
            logger.exception("Branch reconcile unsubscribe failed for topic=%s", topic)
            results[topic] = {"error": f"{type(exc).__name__}: {exc}"}
            any_failure = True

    return {
        "updated": not any_failure,
        "branches": sorted(desired),
        "results": results,
    }


@api_router.post("/admin/notify/test", dependencies=[Depends(require_admin_token)])
async def notify_test(slno: int | None = None):
    """
    Send a single test push to the topic. If `slno` is given, uses that
    notice's real title/body (so we can validate the click-through
    deep-link end-to-end). Otherwise sends a canned diagnostic message.
    """
    if slno is not None:
        notice = await db.notices.find_one({"slno": slno}, {"_id": 0})
        if not notice:
            raise HTTPException(status_code=404, detail=f"slno={slno} not found")
        content = build_notification(notice)
        data = {
            "slno": slno,
            "test": "1",
            "company": notice.get("company") or "",
            "deeplink": f"/entries/{slno}",
            "url": f"/entries/{slno}",
        }
    else:
        content = {
            "title": "Placement Pulse — test push",
            "body": "If you see this, notifications are working.",
        }
        data = {"slno": "0", "test": "1", "deeplink": "/", "url": "/"}

    try:
        message_id = publish_to_topic(
            FCM_TOPIC, title=content["title"], body=content["body"], data=data
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"fcm_publish_failed: {exc}")
    return {"published": True, "message_id": message_id, "content": content, "data": data}


@api_router.post("/admin/notify/mark-backfilled", dependencies=[Depends(require_admin_token)])
async def notify_mark_backfilled():
    """
    Stamp `notifiedAt` on every notice that currently has it null,
    WITHOUT publishing. One-shot for post-deploy cleanup so the
    existing backfilled notices don't fire a 200+ push storm on the
    next scheduled run.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    result = await db.notices.update_many(
        {"notifiedAt": None},
        {"$set": {"notifiedAt": now_iso, "backfilledAt": now_iso}},
    )
    return {"marked": result.modified_count}


@api_router.post("/admin/notify/pending", dependencies=[Depends(require_admin_token)])
async def notify_pending(limit: int = Query(10, ge=1, le=50)):
    """
    Publish pending pushes for entries where `notifiedAt` is still null
    (e.g. because the previous FCM call failed). Retry helper.
    """
    cursor = (
        db.notices.find({"notifiedAt": None}, {"_id": 0})
        .sort([("date", -1), ("slno", -1)])
        .limit(limit)
    )
    pending = await cursor.to_list(length=limit)
    if not pending:
        return {"published": 0, "pending": 0}

    published = 0
    errors = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for notice in pending:
        content = build_notification(notice)
        try:
            publish_to_topic(
                FCM_TOPIC,
                title=content["title"],
                body=content["body"],
                data={
                    "slno": notice["slno"],
                    "company": notice.get("company") or "",
                    "deeplink": f"/entries/{notice['slno']}",
                    "url": f"/entries/{notice['slno']}",
                },
            )
            await db.notices.update_one(
                {"slno": notice["slno"]},
                {"$set": {"notifiedAt": now_iso}},
            )
            published += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"slno={notice['slno']}: {type(exc).__name__}: {exc}")
    return {"published": published, "pending": len(pending), "errors": errors}


# --- /api/stats (Phase 6 · smart enhancement) -------------------------
@api_router.get("/stats")
@limiter.limit("60/minute")
async def stats(request: Request):
    """
    Lightweight public counters for the header strip: total notices,
    this-week count, unique companies, and last-poll timing. Cheap:
    all reads are indexed/aggregations on `notices` + one query on
    `ingest_runs`.
    """
    ist_now = datetime.now(IST)
    iso = ist_now.isocalendar()
    current_week = f"{iso.year}-W{iso.week:02d}"

    total = await db.notices.count_documents({})
    this_week = await db.notices.count_documents({"isoWeek": current_week})

    # Companies (non-empty, distinct)
    distinct_companies = await db.notices.distinct(
        "company", {"company": {"$ne": ""}}
    )

    # Latest notice for a "since" indicator
    latest_doc = await db.notices.find_one(
        {}, {"_id": 0, "slno": 1, "date": 1, "firstSeenAt": 1, "headline1": 1},
        sort=[("date", -1), ("slno", -1)],
    )

    last_run = await db.ingest_runs.find_one(
        {}, {"_id": 0, "finished_at": 1, "fetched": 1, "inserted": 1, "error": 1},
        sort=[("started_at", -1)],
    )

    # Per-company drive counts (top 10, non-empty company only). Drives
    # for our purposes = every notice, not just noticeType=drive, so the
    # student can see "who has posted the most this term".
    pipeline = [
        {"$match": {"company": {"$ne": ""}}},
        {"$group": {"_id": "$company", "count": {"$sum": 1}}},
        {"$sort": {"count": -1, "_id": 1}},
        {"$limit": 10},
        {"$project": {"_id": 0, "company": "$_id", "count": 1}},
    ]
    top_companies = await db.notices.aggregate(pipeline).to_list(length=10)

    return {
        "totalNotices": total,
        "thisWeekCount": this_week,
        "currentWeek": current_week,
        "uniqueCompanies": len(distinct_companies),
        "topCompanies": top_companies,
        "latest": latest_doc,
        "lastPoll": last_run,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }


# --- /api/admin/reprocess (Phase 6 · backfill new fields) ------------
@api_router.post("/admin/reprocess", dependencies=[Depends(require_admin_token)])
async def admin_reprocess(limit: int = Query(500, ge=1, le=5000)):
    """
    Re-derive `ctc`, `branches`, `noticeType`, `closingDate`,
    `messageExcerpt` on already-persisted notices using their stored
    `detailsHtml`. One-shot backfill after Phase 6 rollout — safe to
    re-run; overwrites deterministically.

    NOTE: this does NOT re-derive `registrationLinks` (it has no access
    to the raw college-API upload*/apply_but fields, only the stored
    detailsHtml). To pick up the file-proxy fix on existing notices,
    run a normal `POST /api/admin/ingest/run?notify=false` instead —
    that re-fetches the raw entries and fully re-normalizes them.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    cursor = db.notices.find({}, {"_id": 0}).limit(limit)
    docs = await cursor.to_list(length=limit)

    updated = 0
    ctc_populated = 0
    branches_populated = 0
    type_counts: dict[str, int] = {}

    for d in docs:
        # Reconstruct the ingest-shaped 'raw' entry from stored fields.
        raw = {
            "slno": d.get("slno"),
            "headline1": d.get("headline1", ""),
            "date": d["date"][:10] if d.get("date") else "",
            "details1": d.get("detailsHtml", ""),
            "company": d.get("company", ""),
            "apply_but": "",
        }
        # Use the current year of the stored date to keep the filter honest.
        try:
            year = int(d["date"][:4])
        except Exception:  # noqa: BLE001
            continue
        # We can't re-run normalize_entry directly because it expects
        # the raw college-API shape with DD/MM/YYYY. Instead, reuse the
        # extractors on stored HTML directly.
        from ingest import (
            _plain_text as _pt,
            extract_ctc as _ctc,
            extract_branches as _br,
            extract_notice_type as _nt,
            extract_closing_date as _cd,
        )
        headline_text = d.get("headline1", "") or ""
        details_html = d.get("detailsHtml", "") or ""
        plain = _pt(details_html)
        combined = f"{headline_text}  {plain}"
        is_message_only = bool(d.get("isMessageOnly"))
        ctc = None if is_message_only else _ctc(combined)
        branches = [] if is_message_only else _br(combined)
        ntype = "notice" if is_message_only else _nt(headline_text, plain)
        closing = None if is_message_only else _cd({
            "headline1": headline_text, "detailsHtml": details_html
        })
        # Phase 6.5 · every notice gets an excerpt now.
        excerpt = _pt(details_html, maxlen=200)

        await db.notices.update_one(
            {"slno": d["slno"]},
            {"$set": {
                "ctc": ctc,
                "branches": branches,
                "noticeType": ntype,
                "closingDate": closing,
                "messageExcerpt": excerpt,
                "updatedAt": now_iso,
            }},
        )
        updated += 1
        if ctc:
            ctc_populated += 1
        if branches:
            branches_populated += 1
        type_counts[ntype] = type_counts.get(ntype, 0) + 1

    return {
        "processed": len(docs),
        "updated": updated,
        "ctcPopulated": ctc_populated,
        "branchesPopulated": branches_populated,
        "typeCounts": type_counts,
    }


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup():
    # In-process scheduler is the default for local dev + EC2/container.
    # For Lambda deployments the poller runs as a separate function
    # triggered by EventBridge Scheduler — set RUN_SCHEDULER_INPROC=0
    # in that environment so we don't spin an idle loop per cold-start.
    if os.environ.get("RUN_SCHEDULER_INPROC", "1") != "1":
        logger.info("RUN_SCHEDULER_INPROC=0 → skipping in-process APScheduler")
        return
    scheduler.add_job(
        _scheduled_ingest,
        OrTrigger([_day_trigger, _night_trigger]),
        id="college-api-poll",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler started. Day trigger: %s | Night trigger: %s",
        _day_trigger, _night_trigger,
    )


@app.on_event("shutdown")
async def _shutdown():
    if scheduler.running:
        scheduler.shutdown(wait=False)
    mongo_client.close()