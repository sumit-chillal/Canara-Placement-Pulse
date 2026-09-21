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


### Open follow-ups (as of Phase 6)
- ~~`closingDate` sort is a proxy — revisit once we can extract a
  real deadline from headline/details ("Register by <date>").~~
  **Still open as of Phase 9 — no change; not revisited.**
- Declined the "weekly WhatsApp/Telegram digest" feature suggested as
  a possible Phase 7 addition — Phase 7 kept strictly to the
  originally planned deployment work (backend hosting + Firebase
  Hosting frontend). Digest idea parked as post-launch backlog, still
  unstarted as of Phase 9.


## 2026-08 · Phase 7 (production deployment) — SHIPPED

### Decision: EC2 + Docker, not AWS Lambda (for now)
`backend/lambda_handler.py` (two entry points — `api_handler` via
Mangum for API Gateway, `scheduled_ingest` for EventBridge Scheduler),
`scripts/build-lambda-zip.sh`, and a Secrets Manager fallback in
`firebase_client.py::_fetch_from_secrets_manager` were all written and
are ready for a serverless deploy. **Not used** — deployed to the
college-provided EC2 instance instead, since Lambda needs IAM role /
Secrets Manager access that wasn't available at deploy time.
Lambda migration remains a real option later; the code doesn't need
to change to support it, only the deploy target.

### What shipped
- `Dockerfile.backend` (already existed as the documented EC2/container
  fallback) is now what's actually running in production.
- EC2 instance `i-00951fcff3cec57c0` ("cecplacement"), Ubuntu, region
  `ap-south-1`. Docker + Nginx installed; container run with
  `--restart unless-stopped` so it survives instance reboots.
- **Elastic IP `3.7.18.84`** associated to the instance for a stable
  address (previously the dynamic `ec2-*.compute.amazonaws.com`
  public DNS, which changes on every stop/start and breaks any prior
  cert/config pointing at it).
- **HTTPS via `sslip.io` + Certbot**: Let's Encrypt refuses to certify
  bare IPs or any `*.amazonaws.com` hostname, so `3-7-18-84.sslip.io`
  (a free wildcard-DNS service resolving to the embedded IP) is used
  purely to give the CA something it's willing to certify. Nginx
  reverse-proxies `443 → 127.0.0.1:8001`; the container itself is
  bound to localhost only (`-p 127.0.0.1:8001:8001`), not exposed
  externally. Certbot auto-renewal already configured.
- **Live URLs:** frontend `https://cec-placements23.web.app`
  (Firebase Hosting), backend `https://3-7-18-84.sslip.io` (EC2).
- Security group: only 22 (SSH), 80, 443 open; port 8001 removed
  after confirming Nginx fronting worked.

### Gotchas hit during this deploy (all now documented in `localsetup.md` §7)
- **SSH key leak + rotation.** The EC2 `.pem`/`.ppk` key was pasted in
  plaintext during troubleshooting and had to be treated as
  compromised; college IT re-keyed the instance. `.ppk` (PuTTY format)
  needed `puttygen ... -O private-openssh` conversion before OpenSSH
  on macOS could use it.
- **`docker run --env-file` does not strip quotes** the way
  `python-dotenv` does — `MONGO_URL="mongodb+srv://..."` in `.env`
  broke `pymongo`'s URI parser in production while working fine
  locally. Fixed by stripping quotes from `.env` (`sed`) and adding an
  explicit "no quotes" callout to setup docs.
- **`nano path/that/doesnt/exist`** silently creates an empty file at
  that path rather than erroring — caused a stray `~/backend/.env`
  and `~/backend/secrets/firebase-admin.json` when run from the wrong
  directory. No data was actually lost; the real files were untouched.
- **Associating an Elastic IP releases the old dynamic public IP/DNS
  immediately** — any existing SSH session or cert tied to the old
  hostname stops working the moment the association completes; not a
  fault, just an ordering thing to expect.
- **Mixed content**: the frontend (HTTPS via Firebase Hosting) cannot
  call an `http://` backend — this is what actually forced the
  Nginx+Certbot setup rather than serving the container's plain HTTP
  port directly.


## 2026-08 · Phase 8 (post-launch bug fixes) — SHIPPED

### Registration-link labeling bugs (`ingest.py::_extract_links`)
- **Bare-domain URL leak**: a notice's registration link pasted as
  plain text without `http(s)://` (e.g. `test.aaptor.com/forms/...`)
  was rendering as a raw URL button instead of "Apply" — `_is_url_like`
  only checked for `http://`/`https://`/`www.` prefixes. Fixed with a
  `_BARE_URL_RE` fallback check.
- **Mislabeled attachments**: the structured `upload{N}`/`{ord}_file`
  path was re-deriving every label from keyword regex instead of using
  the college portal's own human-written label — "Registered Students
  List" contains the substring "regist", which false-matched the
  `apply|register|...` pattern and rendered as "Apply". Fixed: the
  portal's own `*_file` label is now used verbatim whenever present
  and not itself URL-like; keyword heuristics are only a fallback for
  when no real label exists.
- **Duplicate identical labels**: multiple JD attachments on one
  notice all resolved to the same label "JD", rendering as
  indistinguishable buttons. New `_finalize_links()` post-processor
  dedupes by destination URL and numbers repeated labels
  (`JD 1`, `JD 2`, ...).

### Notification recycling bug (the big one)
- **Symptom**: a push notification (often the generic batched "N new
  drives" message, rarely a real per-company one) firing on
  effectively every 30-minute scheduled tick, not just when something
  was genuinely new.
- **Root cause**: `notices` is capped at `NOTICE_CAP = 100` and
  trimmed every run (oldest evicted first), but the college API keeps
  re-listing its full current-year notice list on every poll. "Is this
  new?" was judged purely by "not currently present in `notices`" —
  so any notice evicted by the cap and then re-listed by the college
  on a later poll looked new again and re-triggered a push, even
  though students had already seen and been notified about it days
  earlier.
- **Fix**: new `seen_slnos` Mongo collection (slno as `_id`, never
  trimmed) records every slno ever encountered. Notification
  eligibility is now decided against `seen_slnos`, completely
  independent of what the capped `notices` collection currently
  holds for display/storage. `notices` insert/update/cap-trim
  behavior is unchanged.
- **Migration note**: on first deploy of this fix, `seen_slnos` starts
  empty, so a manual `POST /api/admin/ingest/run?notify=false` was run
  immediately after restart to seed it silently before the next
  scheduled (notify=true) tick — documented as a standing procedure in
  `localsetup.md` §9.5 for any future change to this logic.

### Removed the notification batching cap
- `MAX_INDIVIDUAL_PUSHES_PER_RUN = 5` used to collapse any poll with
  more than 5 genuinely-new notices into one generic "N new drives
  posted" push instead of real per-company ones. **Removed entirely**
  per explicit requirement — every genuinely new notice now always
  gets its own individual push with the real company name, no
  exceptions for bursts. Safe now that `seen_slnos` guarantees a false
  burst (cap-eviction recycling, a stale backfill) can't reach this
  code path in the first place — anything arriving here is real.
- `_publish_new_notices()` now also returns `published_slnos` (exactly
  which slnos were confirmed published) so `notifiedAt` is stamped
  precisely, rather than assuming successes are a contiguous prefix of
  the input list.

### iOS Safari push-subscribe race condition (`frontend/src/lib/firebase.js`)
- **Symptom**: subscribing to notifications on iPhone (iOS 18.7,
  Safari/standalone PWA) intermittently failed with two different
  native errors across attempts — "Subscribing for push requires an
  active service worker" and "Failed due to internal service error".
- **Root cause**: `registerServiceWorker()` waited for SW activation
  via a hand-rolled `installing`/`waiting` state-change listener with
  a 4-second fallback timeout that resolved regardless of actual
  state. iOS activates a fresh service worker noticeably slower than
  Chrome/Android, so the timeout could fire before the worker was
  genuinely active, and `getToken()` would then run against a
  not-yet-active registration — exactly what iOS's native
  `PushManager` reports as "requires an active service worker". The
  second error is a known-flaky Apple Web Push provisioning failure
  that typically clears on retry.
- **Fix**: `registerServiceWorker()` now awaits
  `navigator.serviceWorker.ready` (the browser's own guaranteed
  "genuinely active" signal) instead of the hand-rolled timeout. A new
  `getTokenWithRetry()` wrapper gives one silent retry (1.2s apart) on
  `getToken()` to absorb Apple's known transient failures.

### Test credentials / verification notes
- Notification-recycling fix verified via
  `GET /api/admin/ingest/runs?limit=5` on production showing
  `published: 0, publish_batched: false, publish_failed: 0` across
  five consecutive polls where the same ~161 slnos were
  inserted/cap-evicted every cycle with zero re-notifications.


## 2026-08 · Phase 9 (crawler policy & link previews) — SHIPPED

### Decision: actively block search indexing, don't optimize for it
This app has no organic-search audience (students reach it via direct
link or install prompt) and some notice attachments (registration
lists, shortlists) could contain student-identifying information that
must never become publicly searchable. Google Search Console, a
sitemap, and Bing Webmaster Tools were **deliberately not set up** —
none of them make sense for a site being kept out of search indexes.

### What shipped
- `frontend/public/robots.txt` — `Disallow: /` (note: an *empty*
  `Disallow:` value blocks nothing at all — the opposite of intended
  — this was caught and corrected before deploy).
- `frontend/public/index.html` — `<meta name="robots" content="noindex, nofollow">`
  as defense-in-depth alongside `robots.txt`.
- Open Graph tags (`og:title`, `og:description`, `og:image`,
  `og:url`, `og:type`) added to `index.html` — unrelated to search
  engines; these control link-preview appearance when a notice or the
  app is shared in WhatsApp/Telegram groups, which is an expected
  real use case here.