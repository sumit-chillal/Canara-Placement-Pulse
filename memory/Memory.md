# Memory — running log of decisions and gotchas

## 2026-02 · Phase 0 (scaffolding)

### Decisions made
- **Backend:** FastAPI (Python), not Node/Express. Reason: default
  env ships FastAPI on supervisor; nothing about our architecture
  needs Node.
- **Frontend:** CRA + workbox for PWA, not Vite. Reason: same as
  above — no need to churn build tooling.
- **Mongo:** Atlas cluster provided by user
  (`cluster0.fikx7qn.mongodb.net`), DB `placement_pulse`.
- **Firebase:** project `cec-placements23`, topic
  `placement-updates`, VAPID key stored in frontend `.env`.
- **Service account JSON:** user named the file
  (`cec-placements23-firebase-adminsdk-fbsvc-1dc83f219f.json`) but
  did not upload it. Placeholder path in `.env`; must be dropped
  into `/app/backend/secrets/firebase-admin.json` before Phase 3.

### Gotchas noted
- College API returns `upload*` fields as literal `_` when empty.
  Filter those before persisting.
- `date` is `DD/MM/YYYY` (not `MM/DD`) — Indian college. Parse
  with an explicit format string.
- `company` is often empty even when a company name appears in the
  headline. Don't rely on it for filtering; derive from headline if
  needed.
- `details1` is HTML-in-a-string with entities already escaped once;
  it needs unescape + DOMPurify before render.
- FCM Web tokens can rotate silently. Phase 3 must re-POST the token
  to `/api/subscribe` on every app load, not just once at first grant.

### Open questions for the user (revisit before Phase 3)
- Upload the Firebase service account JSON. **[DONE 2026-02 — dropped at `/app/backend/secrets/firebase-admin.json`]**
- Confirm polling interval. **[RESOLVED: IST-aware day/night — 30 min from 08:00–23:30 IST, every 2 h from 00:00–06:00 IST.]**
- Do we want any lightweight admin auth on `/api/admin/*` or leave it IP-restricted only?

## 2026-02 · Phase 1 (ingest pipeline) — SHIPPED and validated live

### What shipped
- `backend/ingest.py`: fetch → filter to current IST year → normalize
  (parse `DD/MM/YYYY`, unescape `details1`, extract company via
  fallback chain, extract links via curated fields then `<a>` fallback,
  compute `isoWeek`, flag `isMessageOnly`) → upsert into `notices`
  keyed by unique `slno` → audit into `ingest_runs`.
- `backend/server.py`: APScheduler `AsyncIOScheduler` with two
  `CronTrigger`s combined via `OrTrigger`, both pinned to
  `ZoneInfo("Asia/Kolkata")` — day: `hour=8-23, minute=0,30`; night:
  `hour=0,2,4,6, minute=0`. Startup registers the job with
  `max_instances=1` + `coalesce=True` so overlapping ticks collapse.
- New admin endpoints (unauthenticated for now — Rules item still open):
  - `POST /api/admin/ingest/run` — one-shot manual poll
  - `GET  /api/admin/ingest/runs?limit=20` — audit log
  - `GET  /api/admin/notices/count` — sanity peek

### End-to-end validation (2026-02-18)
- College API: 8112 raw entries fetched.
- Current-year filter (2026 from IST clock): 222 kept, 7890 skipped.
- Run 1: `inserted=222, updated=0, new_slnos.length=222`.
- Run 2 (immediately after): `inserted=0, updated=222, new_slnos=[]`
  → idempotency confirmed on the live cluster.
- `ist_hour=23` recorded in the audit doc → timezone logic pulling
  from `Asia/Kolkata` (not container UTC) verified.
- Company extraction: 61/222 headlines yield clean names
  (`Storeys Real Estate`, `Radware`, `Incture`, `Codeyoung`…). Rest
  legitimately blank per spec.
- Atlas Network Access allowlist was fixed by the user mid-phase.

### Test credentials
- None yet (public read-only app).


## 2026-02 · Phase 2 (public read API) — SHIPPED

### What shipped
- `GET /api/entries` — `week` (ISO regex-validated), `sort`
  (`latest|company|closingDate`), `limit` (1-200), `cursor` (slno).
  Rate-limited 60/min per IP via slowapi.
- `GET /api/entries/{slno}` — 404 on missing, 120/min per IP.
  Response includes raw `detailsHtml` for client-side DOMPurify.
- `GET /api/health` — extended with `total_notices` + last
  `ingest_runs` doc for external monitoring.
- CORS: switched from `"*"` to explicit `CORS_ORIGINS` env list
  (frontend prod URL + `http://localhost:3000`). Verified via curl:
  evil origins get NO `Access-Control-Allow-Origin` from FastAPI.
  (The Kubernetes ingress adds its own `*` header on top of ours,
  which is a platform-level concern, not a backend bug.)
- Custom `RequestValidationError` handler converts 422 → 400 with
  a structured `{error, errors: [{field, message, type}]}` body.
- Rate-limit response: 429 `{error: rate_limited, detail: ...}`.

### `closingDate` sort — honest interpretation
We don't parse a real deadline field from the college API, so this
sort orders by `date` ascending (soonest posted first). Documented
in `_sort_spec`. If the college API ever exposes a real deadline
field we'll swap the implementation without changing the public
enum.

### Live validation
- Malformed `week=abc` → 400 with regex-mismatch message.
- Impossible `week=2026-W99` → 400.
- Bogus `sort=hackme` → 400 with the exact enum values listed.
- `limit=500` (max is 200) → 400.
- Non-integer path `slno=nope` → 400.
- `slno=99999999` (not in DB) → 404 `{"detail":"Notice slno=… not found"}`.
- 65 back-to-back hits on `/api/entries` → 60 × 200, 5 × 429.
- Cursor pagination: page1 `[10775,10774]` → next=10774 → page2
  `[10773,10772]`.

### Open follow-ups
- ~~`/api/admin/*` still unauthenticated.~~ **RESOLVED in Phase 6** —
  header `X-Admin-Secret` now required on every admin route.
- `closingDate` sort is a proxy — revisit once we can extract a
  real deadline from headline/details ("Register by <date>").


## 2026-02 · Phase 6 — SHIPPED (final, replacing prior draft)

**Header rename note:** the earlier draft used `X-Admin-Secret`/`ADMIN_SECRET`;
final spec is `X-Admin-Token`/`ADMIN_TOKEN`. All routes, tests, and docs
reference the token name.

### Search + filters (Phase 6.1)
- `GET /api/entries` now accepts, all optional and composable:
  - `q`        · case-insensitive substring over `headline1`, `company`,
                 `messageExcerpt` (built as `$or` regex, user input escaped)
  - `company`  · case-insensitive substring on `company`
  - `hasRegistration`  · `true`/`false` filter on presence of any link
  - `noticeType`       · `drive|internship|results|ppt|notice`
  - existing `week`, `sort`, `limit`, `cursor` unchanged and still compose
- Bad `noticeType` → 400 (FastAPI regex validator, mapped by our
  422→400 handler).
- Frontend `FilterBar` (under the hero, per spec) exposes all four
  server params + client-side branch chip filter. `q`/`company` are
  **debounced 300 ms** (setTimeout in Home effect) before the network
  call fires — verified by inspecting the effect deps and the
  `debounceRef` timer.

### Card redesign (Phase 6.2 — the main ask)
- Persisted at ingest time on every notice doc:
  - `ctc`         (e.g. `"6-8 LPA"`, `"8.36 LPA"`, or `null`)
  - `branches`    (`["CSE","ISE","ECE",…]` or `["ALL"]` sentinel)
  - `noticeType`  (drive|internship|results|ppt|notice)
  - `messageExcerpt`  — **extended in Phase 6.5 to every notice, not
     just message-only** (200-char plain-text preview of `detailsHtml`)
- `EntryCard.jsx` renders CTC / branches / type badges (colour-coded
  per type), a 2-line clamped excerpt for all notices, and keeps the
  existing deadline chip, dynamic buttons, and unread accent.
- Fields silently omitted when null — no fake/guessed values.

### Three smart enhancements — verified
- **a) `/api/stats`** — ✅ already scaffolded in prior pass; **completed**
  in this phase by adding a `topCompanies` aggregation (top 10 by count,
  sorted desc; `[{"company": str, "count": int}]`). Response shape:
  `totalNotices, thisWeekCount, currentWeek, uniqueCompanies,
   topCompanies, latest, lastPoll, generatedAt`.
- **b) FCM payload deeplink + company** — ✅ already scaffolded in
  prior pass as `data.url`; **completed** by adding `data.deeplink`
  (canonical name from spec) *and* keeping `data.url` as an alias so
  the existing SW `notificationclick` regex on `/entries/<slno>`
  continues to work. Confirmed via curl round-trip:
  `data = {slno, company, deeplink: "/entries/<slno>", url:
   "/entries/<slno>", ...}`.
- **c) Persistent current-week chip** — ✅ **newly implemented** in
  this phase (was proposed in Phase 4 completion note but not
  actually shipped). `components/CurrentWeekChip.jsx` reads
  `localStorage["pp.currentWeekViewedAt"]` on mount. Home computes
  `currentWeekUnread` = entries in current ISO week whose
  `firstSeenAt > localStorage.pp.currentWeekViewedAt`. Chip shows
  that count when non-zero; tapping the chip both smooth-scrolls
  to the current-week section and updates the localStorage
  timestamp (via `markCurrentWeekViewed()`). `WeekSection` also
  applies a green left-border and "Current" pill on the current
  week, and auto-expands it.

### PWA verification (Lighthouse CLI + manual audit)
Lighthouse 12 removed the "PWA" score bucket, so we ran Lighthouse
against a production `yarn build` served on `127.0.0.1:3001`, plus a
manual per-criterion audit of every installability rule.
- Lighthouse scores (headless Chrome, prod build):
  - Performance 52 · Accessibility **100** · Best Practices **96**
  - `is-on-https`: PASS   ·   `viewport`: PASS
- Manual installability audit (see `/app/tmp/lh/report.report.json`):
  - Manifest served w/ correct MIME; contains name, short_name,
    start_url `/`, `display=standalone`, theme_color `#14140f`,
    background_color, 4 icons.
  - Icons: 192×192 + 512×512 in both `any` and `maskable` purposes,
    all four PNGs reachable (41k/275k/27k/164k bytes).
  - Service worker at `/firebase-messaging-sw.js` (origin root →
    scope=`/`), contains `onBackgroundMessage`, `notificationclick`,
    `openWindow`.
  - `index.html` carries `<link rel="manifest">`, viewport,
    theme-color, apple-touch-icon, apple-mobile-web-app-capable.
  - SW registration handled from bundled JS (present in
    `main.<hash>.js`, verified by grep post-build).
  - `start_url=/` returns HTTP 200 in prod build.
  - HTTPS satisfied via preview URL in production; localhost is
    treated as a secure context by every current browser.

### Admin auth guard
- New env var `ADMIN_TOKEN` (32-byte urlsafe) in `backend/.env`.
- New FastAPI dep `require_admin_token`: reads header `X-Admin-Token`,
  compares constant-time equality, returns 401 on mismatch or 503
  when the env var is empty.
- Applied via `dependencies=[Depends(require_admin_token)]` to every
  admin route: `/admin/ingest/run`, `/admin/ingest/runs`,
  `/admin/notices/count`, `/admin/notify/test`,
  `/admin/notify/mark-backfilled`, `/admin/notify/pending`,
  `/admin/reprocess`.

### FCM
- New service-account JSON dropped in; `google.oauth2.service_account
  .Credentials.refresh()` now returns a live token (verified in
  isolation). Real publish via `/api/admin/notify/test` returns a
  valid `projects/cec-placements23/messages/<id>` message id. All 5
  FCM-publish regression tests that previously skipped now run.

### Test summary (backend)
- **47 passed, 1 skipped** (the message-only publish test skips when
  no `isMessageOnly` notice is present in the current cluster to
  target).
- Phase 6 test file adds coverage for: `/api/entries` `q`, `company`,
  `hasRegistration=true|false`, `noticeType`, filter composition
  (`noticeType + company`, `q + week`), rejection of bad
  `noticeType`, admin token happy + unhappy path (including old
  `X-Admin-Secret` header name explicitly rejected),
  `topCompanies` sort order + shape, `messageExcerpt` universal
  coverage, and standalone extractor unit tests.


### Open follow-ups
- `closingDate` sort is a proxy — revisit once we can extract a
  real deadline from headline/details ("Register by <date>").

