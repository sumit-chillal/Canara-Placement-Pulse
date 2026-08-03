# Architecture

```
                    ┌────────────────────────────────────────┐
                    │  canaraengineering.in/api/news         │
                    │   ?action=all   (JSON, ALL notices)    │
                    └───────────────┬────────────────────────┘
                                    │  (only OUR backend
                                    │   is allowed to call it)
                                    ▼
              ┌──────────────────────────────────────────┐
              │  Placement Pulse Backend (FastAPI)       │
              │                                          │
              │  ┌───────────────────────┐               │
              │  │ APScheduler poller    │──┐            │
              │  │ every N seconds       │  │            │
              │  └───────────────────────┘  │            │
              │            │                │            │
              │            ▼                ▼            │
              │  ┌───────────────────────────────────┐   │
              │  │ Ingest pipeline                   │   │
              │  │  · fetch                          │   │
              │  │  · normalize (parse DD/MM/YYYY,   │   │
              │  │    stable id = slno)              │   │
              │  │  · dedupe against Mongo           │   │
              │  │  · flag "new since last run"      │   │
              │  └───────────────┬───────────────────┘   │
              │                  │                       │
              │        ┌─────────┴─────────┐             │
              │        ▼                   ▼             │
              │  ┌────────────┐    ┌────────────────┐    │
              │  │ MongoDB    │    │ FCM Admin SDK  │    │
              │  │ Atlas      │    │ publish to     │    │
              │  │ (notices)  │    │ topic          │    │
              │  └─────┬──────┘    │ "placement-    │    │
              │        │           │  updates"      │    │
              │        │           └────────┬───────┘    │
              │        │                    │            │
              │  ┌─────┴─────────────┐      │            │
              │  │ REST API          │      │            │
              │  │ /api/notices …    │      │            │
              │  └─────────┬─────────┘      │            │
              └────────────┼────────────────┼────────────┘
                           │                │
                    (HTTPS │)               │ (push)
                           ▼                ▼
              ┌──────────────────────────────────────────┐
              │  Student PWA (React CRA + workbox)       │
              │                                          │
              │  · reads notices from OUR /api           │
              │  · caches them in IndexedDB (idb)        │
              │  · service worker (firebase-messaging-sw)│
              │    subscribes to "placement-updates"     │
              │  · DOMPurify before rendering details1   │
              └──────────────────────────────────────────┘
```

## Key invariants
1. **Only the backend touches the college API.** Not the browser, not a
   Cloud Function, not the CDN. One poller, one server IP.
2. **The college is not trusted to filter or sort.** Every request is
   `?action=all`; we sort/dedupe/filter in our own code.
3. **`slno` is the primary identifier.** We treat higher `slno` as newer
   for tie-breaking, but the persisted `date` (parsed from DD/MM/YYYY)
   is the source of truth for user-facing ordering.
4. **`details1` is untrusted HTML** even though it comes from the
   college CMS. DOMPurify runs before any `dangerouslySetInnerHTML`.
5. **FCM topic model, not per-user tokens.** Backend publishes once per
   new notice; every subscribed device gets it. No per-user fan-out.
6. **Idempotent ingest.** Re-running the poller cannot create duplicate
   notices and cannot re-fire pushes for already-seen `slno`s.

## Data model (Mongo — `placement_pulse` DB)

### `notices`
| field           | type      | notes                                      |
|-----------------|-----------|--------------------------------------------|
| `_id`           | string    | equals `slno` from college API             |
| `slno`          | string    | duplicate for query convenience            |
| `headline1`     | string    |                                            |
| `date_raw`      | string    | original `"DD/MM/YYYY"`                    |
| `date`          | ISO date  | parsed                                     |
| `details_html`  | string    | raw `details1` (sanitized at render)       |
| `company`       | string?   | often empty upstream                       |
| `files`         | string[]  | `first_file`…`fifth_file` filtered non-`_` |
| `uploads`       | string[]  | `upload1`…`upload5` filtered non-`_`       |
| `apply_but`     | string?   |                                            |
| `first_seen_at` | ISO date  | when our poller first saw it               |
| `notified_at`   | ISO date? | when we published to FCM                   |

### `ingest_runs` (observability)
| field          | type      | notes                                    |
|----------------|-----------|------------------------------------------|
| `started_at`   | ISO date  |                                          |
| `finished_at`  | ISO date  |                                          |
| `fetched`      | int       | count returned by college API            |
| `inserted`     | int       | new notices this run                     |
| `notified`     | int       | pushes actually sent                     |
| `error`        | string?   |                                          |

## Service topology in this repo
```
/app
├── backend/                 FastAPI + APScheduler + firebase-admin
│   ├── server.py            app entrypoint (currently: health check)
│   ├── .env                 real values (git-ignored)
│   ├── .env.example         template
│   └── secrets/             firebase-admin.json goes here (git-ignored)
├── frontend/                React CRA PWA
│   ├── public/
│   │   ├── manifest.json    PWA manifest
│   │   └── firebase-messaging-sw.js
│   └── src/
│       ├── App.js           scaffold landing page
│       └── lib/firebase.js  FCM client singleton
└── memory/                  PRD, Architecture, Rules, Phases, Design, Memory
```
