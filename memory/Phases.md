# Phases

Each phase is small, ends with something demoable, and does not
pretend to be the next phase.

## Phase 0 — Scaffolding (current, DONE 2026-02)
**Goal:** empty app that runs end-to-end.
- FastAPI `/api/` + `/api/health` (Mongo ping + config echo).
- CRA frontend landing page that hits `/api/health` and shows status.
- `.env` populated with Mongo Atlas URI + Firebase project config.
- `firebase-messaging-sw.js` in place (not yet activated by the app).
- Docs (PRD, Architecture, Rules, Phases, Design, Memory).

**Definition of done:** loading the site shows the scaffold card with a
green "Backend ok · Mongo connected" chip.

## Phase 1 — Ingest pipeline
**Goal:** notices flow from the college API into Mongo.
- APScheduler job polling `?action=all` every `POLLING_INTERVAL_SECONDS`.
- Normalization: parse `DD/MM/YYYY` → ISO, collect files/uploads
  arrays, strip empty `_` placeholders.
- Upsert on `slno`. Mark `first_seen_at` on insert only.
- `ingest_runs` audit log.
- Manual endpoint `POST /api/admin/ingest/run` (internal-only later).

**Definition of done:** Mongo has ≥ 1 real notice from the live college
API; running the poller twice does not duplicate.

## Phase 2 — Read API
**Goal:** frontend can list and read notices from our DB.
- `GET /api/notices?limit=&cursor=&q=&company=` — paginated list.
- `GET /api/notices/{slno}` — full detail.
- No writes from the client.
- Response shape stable and documented.

**Definition of done:** curl returns notices sorted by `date` desc,
then `slno` desc, with sanitized-safe HTML in the payload (still
untrusted; frontend must DOMPurify).

## Phase 3 — Push
**Goal:** new notices trigger an FCM push.
- Firebase Admin init from `FIREBASE_SERVICE_ACCOUNT_PATH`.
- On new insert with `notified_at == null`: publish to topic
  `placement-updates` (`title = headline1`, deeplink `data.url = /n/<slno>`).
- Set `notified_at` on success.
- Frontend: request permission, get FCM token, POST to
  `/api/subscribe` which server-side calls
  `messaging().subscribeToTopic(token, 'placement-updates')`.
- Foreground handler in the app shell (in-app toast).
- Background handled by `firebase-messaging-sw.js` (already stubbed).

**Definition of done:** on-device notification fires within a minute
of a fresh notice landing in Mongo.

## Phase 4 — Frontend UX
- Notice list (virtualised), detail view with sanitized HTML render.
- Search + filter (company, date range, keyword).
- Bookmarks (IndexedDB, per-device).
- Install prompt + "Add to home screen" flow.
- Offline: last N notices readable without network.

## Phase 5 — Polish
- Empty / error / loading states.
- Notification history screen.
- Dark theme.
- Accessibility pass (focus rings, aria labels).

## Phase 6 (P2) — Optional
- Admin dashboard for placement cell.
- Weekly digest email / share sheet.
- Basic analytics (opens per notice, subscriber count).
