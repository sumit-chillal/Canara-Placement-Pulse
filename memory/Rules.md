# Rules — non-negotiables for this codebase

## Data source
1. **Only** `GET https://canaraengineering.in/api/news?action=all` is
   allowed. Do not add code paths that use `action=single` or
   `action=latest`. They are unreliable (broken param / edit-time sort).
2. The **backend** is the only thing that ever calls the college API.
   The browser must never touch `canaraengineering.in` directly — not
   for JSON, not for images, not for file downloads (proxy files
   through our backend if needed).
3. Do not trust the college API to sort, dedupe, or filter. We do all
   of that server-side against Mongo.
4. `slno` is the stable identifier. Don't invent your own IDs. Don't
   use `headline1` or `date` for identity.

## Push notifications
5. FCM is **topic-based** (`placement-updates`). We do not maintain a
   per-user token table for fan-out.
6. Publish exactly **once per new `slno`**. A re-run of the poller must
   not re-publish notices we've already sent (`notified_at` gates it).
7. Never publish from the browser. Only the backend, via the Firebase
   Admin SDK with the service-account JSON.

## Security & rendering
8. `details1` HTML is untrusted. Always run it through DOMPurify before
   rendering. No exceptions, no "but it's our own college".
9. No credentials in code. Everything through `.env`. The service
   account JSON lives under `backend/secrets/` and is git-ignored.
10. CORS: allowed origins come from `CORS_ORIGINS` env var.

## Frontend
11. Offline cache uses **IndexedDB via `idb`**. Not `localStorage`.
12. Service worker (`firebase-messaging-sw.js`) is served from `/public`
    at the site root. Don't move it.
13. Any interactive/user-facing element needs a `data-testid`.

## Backend
14. All routes are prefixed with `/api`. The ingress routes anything
    starting with `/api` to port 8001.
15. Bind address stays `0.0.0.0:8001`. Supervisor is the only process
    manager. Don't run uvicorn manually.
16. Use `datetime.now(timezone.utc)`. Never `datetime.utcnow()`. Store
    datetimes as ISO strings in Mongo.

## Tooling
17. `yarn`, not `npm`.
18. Update `package.json` via `yarn add`, `requirements.txt` by
    installing then pinning — never by hand-editing to random versions.
19. Don't restart supervisor for regular code edits; hot reload handles
    it. Restart only after `.env` or dependency changes.
