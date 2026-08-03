# Placement Pulse — Product Requirements (PRD)

_Last updated: 2026-02 (scaffolding phase)_

## 1. Problem
Engineering students at Canara Engineering College miss placement and
internship notices because the college portal is slow to load, has no
push mechanism, and gets hammered when 1000+ students refresh at once.
Students find out late, miss registration deadlines, and lose real
opportunities.

## 2. Vision
A fast, installable PWA that pushes new placement/internship drives to
students the moment they're posted, works offline for previously seen
notices, and never once puts load on the college server from the
student's device.

## 3. Users & personas
- **Final-year student (primary):** wants instant notification of eligible
  drives, quick access to the details, and the ability to bookmark and
  filter (by company, eligibility, date).
- **Pre-final / internship-hunting student:** wants internship-tagged
  notices specifically, plus a way to catch up on what was posted this
  week.
- **Placement cell staff (read-only, later):** may want a dashboard to
  verify the app is showing what the portal shows.

## 4. Core requirements (fixed)
- **Data source:** ONLY `https://canaraengineering.in/api/news?action=all`.
  Never `action=single`, never `action=latest`.
- **Architecture:** one backend poller → Mongo Atlas → FCM topic
  `placement-updates` → all student PWAs. Devices never call the
  college API directly.
- **Sanitize before render:** every `details1` HTML string must pass
  through DOMPurify.
- **Offline:** last-known notices readable offline via IndexedDB.
- **Installable:** PWA manifest + service worker on all major browsers.

## 5. Non-goals (for now)
- Auth / login (public, read-only app in v1).
- Comments, reactions, chat.
- Any writing to the college's system.
- Native iOS/Android apps (PWA is the deliverable).

## 6. Success metrics (post-launch)
- ≥ 90 % of newly posted drives result in a push within 10 min of
  appearing on the college site.
- 0 direct hits on `canaraengineering.in` from student browsers.
- Median time-to-first-content on the notice list < 1 s on 4G.

## 7. What's implemented so far
- **2026-02 · Phase 0 (scaffolding):**
  - FastAPI backend with `/api/health` verifying Mongo Atlas connectivity.
  - React CRA frontend showing a scaffold landing page + live backend
    health status.
  - `firebase-messaging-sw.js` registered, ready to receive FCM pushes.
  - `.env` / `.env.example` for Mongo Atlas, college API URL, FCM topic,
    Firebase project.
  - Docs: PRD, Architecture, Rules, Phases, Design, Memory.
- **2026-02 · Phase 2 (public read API):**
  - `GET /api/entries?week=&sort=&limit=&cursor=` — validated query
    params, 60/min per-IP rate limit, cursor pagination on `slno`.
  - `GET /api/entries/{slno}` — full detail incl. raw `detailsHtml`
    (client sanitizes with DOMPurify at render). 120/min per-IP.
  - `GET /api/health` — now includes `total_notices` and the last
    `ingest_runs` snapshot for monitoring.
  - CORS locked to explicit `CORS_ORIGINS` list (no wildcard).
  - Validation errors return **400** (not 422) via a
    `RequestValidationError` handler; malformed `week` / `sort` /
    `limit` / non-int `slno` all yield structured 400 responses.
  - Verified live: cursor paging, 404 for missing slno, 60/min
    rate-limit kicks in exactly at request 61.
- **2026-02 · Phase 2 (public read API):**
  - `GET /api/entries?week=&sort=&limit=&cursor=` — validated query
    params, 60/min per-IP rate limit, cursor pagination on `slno`.
  - `GET /api/entries/{slno}` — full detail incl. raw `detailsHtml`
    (client sanitizes with DOMPurify at render). 120/min per-IP.
  - `GET /api/health` — now includes `total_notices` and the last
    `ingest_runs` snapshot for monitoring.
  - CORS locked to explicit `CORS_ORIGINS` list (no wildcard).
  - Validation errors return **400** (not 422) via a
    `RequestValidationError` handler; malformed `week` / `sort` /
    `limit` / non-int `slno` all yield structured 400 responses.
  - Verified live: cursor paging, 404 for missing slno, 60/min
    rate-limit kicks in exactly at request 61.
- **2026-02 · Phase 3 (FCM push + deep-link):** Firebase Admin publish
  on new entries with 3-rule notification content; `/api/subscribe`
  binds tokens to topic; SW `notificationclick` deep-links to
  `/entries/<slno>`. 8/8 pytest.
- **2026-02 · Phase 4 (PWA shell + install):** manifest with 4 icons
  (any + maskable @192/512), apple-touch-icon + iOS meta, hero photo,
  logo topbar, iOS "Add to Home Screen" onboarding, Android
  `beforeinstallprompt` flow, Badging API guarded by feature detect.
- **2026-02 · Phase 5 (main UI + offline + unread):** weekly collapsible
  sections, client-side sort (newest/oldest/company/recent),
  responsive card grid, dynamic buttons (1/many/none links),
  DOMPurify-sanitized detail, `idb` cache-then-network with
  offline-chip fallback, unread badge via lastSeenSlno in IDB.
  Backend now precomputes `closingDate` + `messageExcerpt`, list
  endpoint drops `detailsHtml` (payload −95%), smarter attachment
  labels (JD / Eligibility / Apply / Results / Circular /
  Presentation). 17/17 backend + 7/7 frontend regression pass.
- **2026-02 · Phase 6 (search, filters, badges, admin auth, stats):**
  - Backend: ingest-time extractors for `ctc` (e.g. "6-8 LPA"),
    `branches` (canonical CSE/ISE/ECE/… + `ALL` sentinel), and
    `noticeType` (drive / internship / results / ppt / notice).
    All three persisted on the notice doc so cards never re-parse.
  - `GET /api/stats` (public, 60/min): totalNotices, thisWeekCount,
    currentWeek, uniqueCompanies, latest, lastPoll.
  - Admin auth guard: every `/api/admin/*` route now requires header
    `X-Admin-Secret: $ADMIN_SECRET`. Missing env → 503; wrong or
    missing header → 401. `ADMIN_SECRET` is a fresh 32-byte
    urlsafe secret in `backend/.env`.
  - `POST /api/admin/reprocess` — one-shot backfill that re-derives
    ctc/branches/noticeType/closingDate/messageExcerpt on already
    stored notices. Ran once against the live cluster: 222
    processed, 56 ctc, 85 branches populated, typeCounts split
    across all five buckets.
  - FCM payload upgraded: publish `data` now carries `slno`,
    `company`, and `url` (`/entries/<slno>`) so the SW can deep-link
    without regex-parsing.
  - Frontend: `FilterBar` under the hero — full-text search,
    company dropdown, notice-type dropdown, minimum-CTC dropdown,
    branch multi-chip; reset button when any filter is active.
    `EntryCard` redesign — CTC badge (moss green), branch badges
    (up to 3 + "+N"), notice-type badge colour-coded per bucket.
    `StatsStrip` above filters shows total / this-week / companies
    / last-synced. `CurrentWeekChip` pins top-right after scroll,
    tap → smooth-scroll to the current-week section.
  - 32/32 non-FCM-publish backend tests green (9 Phase 5 + 20 Phase
    6 + 3 Phase 3 non-publish). 5 FCM-publish tests skip in this
    env because the service-account JWT is being rejected by
    Google (invalid_grant) — pre-existing infra concern, not a
    Phase-6 regression.

## 8. Prioritised backlog
- **P0 — Phase 1 (data pipeline):** Poller that hits `?action=all` on a
  timer, dedupes by `slno`, upserts into Mongo, marks new entries.
- **P0 — Phase 2 (read API):** `/api/notices` list + detail endpoints
  with pagination, filtering, sanitized HTML.
- **P0 — Phase 3 (push):** FCM Admin SDK publisher on new entries;
  frontend token registration + subscribe-to-topic flow.
- **P1 — Phase 4 (frontend UX):** notice list, detail view, search,
  filters, bookmarks, install prompt, offline via IndexedDB.
- **P1 — Phase 5 (polish):** empty states, error handling, in-app
  notification history, dark mode.
- **P2:** admin dashboard, analytics, weekly digest.
