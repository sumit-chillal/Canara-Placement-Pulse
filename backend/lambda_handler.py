"""AWS Lambda entry-points for Placement Pulse.

Two handlers live in this file so a single deployment bundle can be
reused by two Lambda functions:

    api_handler        →  API Gateway HTTP integration (FastAPI via Mangum)
    scheduled_ingest   →  EventBridge Scheduler (IST day/night cadence)

Both share the same code, deps, and env vars — the only difference is
which handler the Lambda invokes.

Local dev keeps using `uvicorn server:app` + in-process APScheduler;
production toggles to this file. Nothing in server.py changed except
that scheduler.start() is guarded by `RUN_SCHEDULER_INPROC=1` (default
"1" locally, "0" in Lambda) so we don't try to spin an APScheduler
loop inside a stateless Lambda container.

Deploy:
    zip -r pp-backend.zip backend/ && aws lambda update-function-code ...

    Function 1 (API):
        handler       = backend.lambda_handler.api_handler
        env           = RUN_SCHEDULER_INPROC=0
        integration   = API Gateway v2 HTTP API, path /{proxy+}
    Function 2 (Poller):
        handler       = backend.lambda_handler.scheduled_ingest
        env           = RUN_SCHEDULER_INPROC=0
        trigger       = 2 × EventBridge Scheduler rules
                        (day: cron(0/5 3-18 * * ? *)  UTC  = 08:30-23:59 IST every 5 min)
                        (night: cron(0 18-23,0-2 * * ? *) UTC = 23:30-08:00 IST hourly)
"""

import asyncio
import logging
import os

# Ensure the in-process APScheduler doesn't start under Lambda.
os.environ.setdefault("RUN_SCHEDULER_INPROC", "0")

from mangum import Mangum          # noqa: E402
from server import app, run_ingest, COLLEGE_API_URL, FCM_TOPIC  # noqa: E402

log = logging.getLogger("pp.lambda")
log.setLevel(logging.INFO)


# --- Handler 1: FastAPI behind API Gateway --------------------------
api_handler = Mangum(app, lifespan="off")


# --- Handler 2: EventBridge-triggered poller ------------------------
def scheduled_ingest(event, context):
    """
    Invoked by EventBridge Scheduler. Runs one ingest cycle then
    returns. Idempotency (upsert on slno) is enforced inside
    run_ingest() — safe to invoke as often as EventBridge fires.

    Event shape (from EventBridge Scheduler) is opaque; we ignore it
    and always run the full cycle with FCM notifications enabled.

    IMPORTANT: this creates its own Motor client scoped to this
    invocation's event loop, rather than reusing server.py's
    module-level `db`. Motor clients are bound to the event loop
    active when they're first used, and a fresh asyncio.new_event_loop()
    per invocation (as below) is a *different* loop than whatever the
    module-level client was bound to at cold start — reusing it across
    loops can throw "Task attached to a different loop" on warm
    (container-reused) invocations. A loop-local client avoids that
    entirely, at the cost of a new connection per invocation, which is
    cheap at this function's polling cadence.
    """
    from motor.motor_asyncio import AsyncIOMotorClient

    notify = str(event.get("notify", "true")).lower() != "false" if isinstance(event, dict) else True

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = None
    try:
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        local_db = client[os.environ["DB_NAME"]]
        result = loop.run_until_complete(
            run_ingest(local_db, COLLEGE_API_URL, notify=notify, notify_topic=FCM_TOPIC)
        )
    finally:
        if client is not None:
            client.close()
        loop.close()

    log.info(
        "scheduled_ingest done: fetched=%s inserted=%s updated=%s published=%s err=%s",
        result.get("fetched"), result.get("inserted"), result.get("updated"),
        result.get("published"), result.get("error"),
    )
    return {
        "statusCode": 200,
        "fetched":  result.get("fetched"),
        "inserted": result.get("inserted"),
        "updated":  result.get("updated"),
        "published": result.get("published"),
        "error":    result.get("error"),
    }