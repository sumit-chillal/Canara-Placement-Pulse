# Placement Pulse — Local Setup (VS Code)

This guide walks a new developer through cloning the repo, wiring the
credentials, installing dependencies, and running both the backend and
the PWA frontend on their laptop. Section 9 covers the actual
production deployment (EC2 + Docker + Nginx), for anyone maintaining
the live instance rather than developing locally.

Everything below is what actually ships in `/app`; adjust paths only if
you clone elsewhere.

---

## 1 · Prerequisites

| Tool           | Version                | Install                                    |
|----------------|------------------------|--------------------------------------------|
| Python         | 3.11 (exactly)         | https://www.python.org/downloads/          |
| Node.js        | 18 or 20 LTS           | https://nodejs.org/                        |
| Yarn (classic) | 1.22.x                 | `npm i -g yarn`                            |
| MongoDB        | Atlas free tier (M0)   | https://cloud.mongodb.com/  (no local mongo needed) |
| Git            | any                    | https://git-scm.com/                       |
| VS Code        | latest                 | https://code.visualstudio.com/             |

Recommended VS Code extensions (they surface the tooling below automatically):

- ms-python.python
- ms-python.vscode-pylance
- charliermarsh.ruff  (matches the lint config already in the repo)
- esbenp.prettier-vscode
- dbaeumer.vscode-eslint
- ms-azuretools.vscode-docker  (needed if you're working on the production Docker image — see §9)

---

## 2 · Clone

```bash
git clone https://github.com/sumit-chillal/Canara-Placement-Pulse.git
cd Canara-Placement-Pulse
code .
```

Directory layout you should now see:

```
Canara-Placement-Pulse/
├── backend/                 FastAPI + APScheduler
│   ├── server.py
│   ├── ingest.py
│   ├── firebase_client.py
│   ├── lambda_handler.py    AWS Lambda entry (prepared, not currently deployed)
│   ├── requirements.txt
│   ├── tests/
│   ├── secrets/             ← you create this (git-ignored)
│   └── .env                 ← you create this (git-ignored)
├── frontend/                Create-React-App PWA
│   ├── src/
│   ├── public/
│   ├── package.json
│   ├── firebase.json        Firebase Hosting config
│   ├── .firebaserc
│   └── .env                 ← you create this (git-ignored)
├── memory/                  PRD, architecture, phases, memory
├── scripts/                 build-lambda-zip.sh, etc.
├── tests/                   (top-level integration, if any)
└── Dockerfile.backend       Production image (EC2) — see §9
```

---

## 3 · Backend

### 3.1 Create a Python venv

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate     # (Windows: .venv\Scripts\Activate.ps1)
pip install --upgrade pip
pip install -r requirements.txt
```

### 3.2 Create `backend/.env`

Contents (**local development**, values are examples — replace the
Mongo URI with your own Atlas cluster):

```dotenv
MONGO_URL=mongodb+srv://<user>:<password>@<cluster>.mongodb.net/?appName=Cluster0
DB_NAME=placement_pulse
CORS_ORIGINS=http://localhost:3000
COLLEGE_API_URL=<API_URL>
POLLING_INTERVAL_SECONDS=300
FCM_TOPIC=placement-updates
FIREBASE_PROJECT_ID=<ID_NAME>
FIREBASE_SERVICE_ACCOUNT_PATH=./secrets/firebase-admin.json
ADMIN_TOKEN=<generate with: python -c 'import secrets;print(secrets.token_urlsafe(32))'>
DISCORD_WEBHOOK_URL=<optional — Discord channel webhook for ingest-failure alerts>

# Optional. Default "1" runs APScheduler in-process (dev + EC2
# production both use "1" — only Lambda uses "0", see §9.4).
RUN_SCHEDULER_INPROC=1
```

> **No quotes around values.** Write `MONGO_URL=mongodb+srv://...`, not
> `MONGO_URL="mongodb+srv://..."`. `python-dotenv` (used for local
> `uvicorn` runs) strips surrounding quotes automatically, but
> `docker run --env-file` — used in production, §9 — does **not**. A
> quoted value there gets passed through literally, quote character
> and all, and breaks `pymongo`'s URI parser with `Invalid URI scheme`.
> Keeping `.env` quote-free everywhere avoids the inconsistency
> entirely, local or production.

Notes:
- `MONGO_URL` — get it from the MongoDB Atlas UI ("Connect → Drivers").
  Add your laptop's public IP to the Atlas IP allow-list (Atlas →
  Network Access), or allow `0.0.0.0/0` for a dev cluster — just
  remember to remove that entry again once you're not actively using
  it, since it allows any IP on the internet to attempt a connection.
- `CORS_ORIGINS` — comma-separated list of every frontend origin that
  is allowed to call the API. `http://localhost:3000` for dev; the
  production value also needs the Firebase Hosting URL(s) — see §9.
- `COLLEGE_API_URL` — must not be empty. If this is blank, `ingest.py`
  fails every poll trying to fetch an empty string.
- `ADMIN_TOKEN` — required, but only for `/api/admin/*` routes; the
  public endpoints don't need it.

### 3.3 Create `backend/secrets/firebase-admin.json`

Download it from the Firebase Console:

1. https://console.firebase.google.com/ → your project.
2. **Project settings** → **Service accounts** → **Generate new private key**.
3. Save the downloaded JSON as `backend/secrets/firebase-admin.json`.

Expected file shape (values redacted):

```json
{
  "type": "service_account",
  "project_id": "cec-placements23",
  "private_key_id": "<hex>",
  "private_key": "-----BEGIN PRIVATE KEY-----\n<base64 blob>\n-----END PRIVATE KEY-----\n",
  "client_email": "firebase-adminsdk-<slug>@cec-placements23.iam.gserviceaccount.com",
  "client_id": "<num>",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
  "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/firebase-adminsdk-<slug>%40cec-placements23.iam.gserviceaccount.com",
  "universe_domain": "googleapis.com"
}
```

`backend/secrets/` is git-ignored by default; never commit this file.

### 3.4 Run the backend

```bash
uvicorn server:app --host 0.0.0.0 --port 8001 --reload
```

Verify:

```bash
curl http://localhost:8001/api/health
```

Expected: JSON with `"status":"ok"`, `mongo.ok:true`, `scheduler_running:true`,
and a positive `total_notices` count once the first poll completes.

Run the tests:

```bash
# in a second shell, still inside the venv
cd backend
REACT_APP_BACKEND_URL=http://localhost:8001 \
ADMIN_TOKEN="<same value as in .env>" \
pytest -q
```

---

## 4 · Frontend

### 4.1 Install deps

```bash
cd frontend
yarn install         # ← always `yarn`, never `npm`
```

`yarn install` reads `package.json` and generates/updates `yarn.lock`.
It creates a local `node_modules/` folder (git-ignored by design —
never commit it; it's ~400 MB of installed packages).

The key packages you'll see in `node_modules/`:

| Package                        | Why it's here                             |
|--------------------------------|-------------------------------------------|
| `react`, `react-dom`           | UI runtime                                |
| `react-router-dom`             | Client-side routing                       |
| `axios`                        | HTTP calls to `/api/*`                    |
| `firebase`                     | FCM web SDK + service-worker glue         |
| `idb`                          | IndexedDB helper (offline cache, lastSeenSlno) |
| `dompurify`                    | Sanitize `detailsHtml` on the detail page |
| `@radix-ui/*`                  | shadcn/ui primitive components            |
| `tailwindcss`, `postcss`, `autoprefixer` | Styling pipeline                |
| `@craco/craco`, `react-scripts`| CRA + custom webpack overrides            |
| `sonner`                       | Toast notifications                       |

If `yarn install` fails on `node-gyp` or a native module, install the
platform build tools (Xcode CLT on macOS, `build-essential` on Linux)
and retry.

### 4.2 Create `frontend/.env`

For local dev pointing at the local backend:

```dotenv
REACT_APP_BACKEND_URL=http://localhost:8001
WDS_SOCKET_PORT=443
ENABLE_HEALTH_CHECK=false

# --- Firebase Web SDK config ---
# All of these are PUBLIC by design (they end up in the client
# bundle). Nothing sensitive lives in this file.
REACT_APP_FIREBASE_API_KEY=<from Firebase console → Project settings → Web app>
REACT_APP_FIREBASE_AUTH_DOMAIN=cec-placements23.firebaseapp.com
REACT_APP_FIREBASE_PROJECT_ID=<Project_ID>
REACT_APP_FIREBASE_STORAGE_BUCKET=<FIREBASE_STORAGE_BUCKET.app>
REACT_APP_FIREBASE_MESSAGING_SENDER_ID=<sender id from console>
REACT_APP_FIREBASE_APP_ID=<web app id from console>
REACT_APP_FIREBASE_MEASUREMENT_ID=<optional: G-XXXXX from Analytics>
REACT_APP_FIREBASE_VAPID_KEY=<from Cloud Messaging → Web Push certificates>
REACT_APP_FCM_TOPIC=placement-updates
```

CRA only exposes env vars prefixed with `REACT_APP_` to the browser
bundle — that's intentional. Anything else (e.g. Mongo URI, admin
token, service-account JSON) must live in the **backend** env only
and MUST NEVER be added to `frontend/.env`.

**Production** `frontend/.env` differs in exactly one line —
`REACT_APP_BACKEND_URL=https://3-7-18-84.sslip.io` instead of
`localhost:8001` — everything else is identical. See §9.3.

### 4.3 Run the frontend

```bash
yarn start
```

Opens `http://localhost:3000`. It will call `REACT_APP_BACKEND_URL/api/*`.

For a production build:

```bash
yarn build         # writes to frontend/build/
# then optionally serve it locally:
npx serve -s build -l 3001
```

To actually deploy that build (not just preview it locally):

```bash
firebase deploy --only hosting
```

---

## 5 · One-off admin operations

All `/api/admin/*` routes require the header `X-Admin-Token: <ADMIN_TOKEN>`.
Swap the base URL for `https://3-7-18-84.sslip.io` to run these against
production instead of local.

```bash
TOKEN=$(grep ADMIN_TOKEN backend/.env | cut -d '=' -f2)

# Trigger one manual ingest (fetches the college API, upserts on slno)
curl -X POST -H "X-Admin-Token: $TOKEN" \
  http://localhost:8001/api/admin/ingest/run

# Trigger a SILENT ingest (no FCM pushes) — use this after any deploy
# that changes ingest.py's notification-eligibility logic, so newly
# "discovered" entries get recorded without spamming a push. See the
# seen_slnos note in §9.5.
curl -X POST -H "X-Admin-Token: $TOKEN" \
  "http://localhost:8001/api/admin/ingest/run?notify=false"

# Backfill the Phase 6 extracted fields on already-stored notices
# (does NOT touch registrationLinks — re-run a normal ingest for that)
curl -X POST -H "X-Admin-Token: $TOKEN" \
  "http://localhost:8001/api/admin/reprocess?limit=500"

# Send a single test push (real FCM — reaches every current subscriber,
# there is no test-only topic, so be deliberate about when you run this
# in production)
curl -X POST -H "X-Admin-Token: $TOKEN" \
  "http://localhost:8001/api/admin/notify/test?slno=<any_real_slno>"

# See recent poll runs
curl -H "X-Admin-Token: $TOKEN" \
  "http://localhost:8001/api/admin/ingest/runs?limit=10"
```

---

## 6 · VS Code launch configs (optional but handy)

Create `.vscode/launch.json`:

```jsonc
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Backend: uvicorn (reload)",
      "type": "debugpy",
      "request": "launch",
      "module": "uvicorn",
      "args": ["server:app", "--host", "0.0.0.0", "--port", "8001", "--reload"],
      "cwd": "${workspaceFolder}/backend",
      "python": "${workspaceFolder}/backend/.venv/bin/python",
      "envFile": "${workspaceFolder}/backend/.env"
    },
    {
      "name": "Backend: pytest (current file)",
      "type": "debugpy",
      "request": "launch",
      "module": "pytest",
      "args": ["-q", "${file}"],
      "cwd": "${workspaceFolder}/backend",
      "python": "${workspaceFolder}/backend/.venv/bin/python",
      "envFile": "${workspaceFolder}/backend/.env",
      "env": {
        "REACT_APP_BACKEND_URL": "http://localhost:8001"
      }
    }
  ]
}
```

Add `.vscode/settings.json` so lint + format are consistent:

```jsonc
{
  "python.defaultInterpreterPath": "${workspaceFolder}/backend/.venv/bin/python",
  "python.testing.pytestEnabled": true,
  "python.testing.pytestArgs": ["backend/tests"],
  "editor.formatOnSave": true,
  "[python]":     { "editor.defaultFormatter": "charliermarsh.ruff" },
  "[javascript]": { "editor.defaultFormatter": "esbenp.prettier-vscode" },
  "[typescript]": { "editor.defaultFormatter": "esbenp.prettier-vscode" }
}
```

---

## 7 · Gotchas seen in practice

- **Mongo connection stalls** — Atlas Network Access allow-list.
  Add your public IP (Google "what is my ip") to the Atlas project.
- **`.env` values wrapped in quotes break production, not local.**
  `docker run --env-file` does not strip `"..."`/`'...'` — the value
  is passed through literally, quote character included, and
  `pymongo` (or anything else parsing that var) breaks immediately.
  `python-dotenv` (local `uvicorn`) silently strips quotes, which is
  exactly why this bug only shows up after deploying, not in local
  dev. Keep `.env` quote-free everywhere to avoid the mismatch.
- **FCM `invalid_grant: Invalid JWT Signature`** — either the
  service-account key was rotated on the Firebase console (regenerate)
  or your machine's clock is skewed by > 5 minutes (fix NTP).
- **`yarn start` compiles but the app is blank** — check the browser
  devtools; nearly always a missing `REACT_APP_*` env var (CRA requires
  a restart of `yarn start` after changing `.env`).
- **`nano some/path/file` on a path that doesn't exist yet** creates a
  new empty file there instead of erroring — easy to accidentally edit
  a stray file if you're not in the directory you think you're in.
  `pwd` before editing config files if anything seems off.
- **A browser will refuse to call an `http://` API from an `https://`
  page** ("mixed content") — the production backend must be served
  over HTTPS, not just the frontend. See §9.2.
- **APScheduler double-firing under `--reload`** — this is a known CRA
  reloader interaction; harmless in dev because the ingest is
  idempotent (upsert on `slno`). If it annoys you, run without
  `--reload`.

---

## 8 · What lives where (summary)

| File / dir                                      | Purpose                                         |
|-------------------------------------------------|-------------------------------------------------|
| `backend/.env`                                  | Mongo, admin token, FCM topic, service-account path (git-ignored) |
| `backend/secrets/firebase-admin.json`           | Firebase service-account private key (git-ignored) |
| `backend/lambda_handler.py`                     | AWS Lambda entry (`api_handler`, `scheduled_ingest`) — prepared, not currently deployed |
| `Dockerfile.backend`                            | Production image — what actually runs on EC2, see §9 |
| `scripts/build-lambda-zip.sh`                   | Produces `pp-backend-lambda.zip` for a future AWS Lambda deploy |
| `frontend/.env`                                 | `REACT_APP_*` only — everything here ships in the browser bundle |
| `frontend/firebase.json`, `frontend/.firebaserc`| Firebase Hosting deploy config                  |
| `frontend/build/`                               | Output of `yarn build`; what Firebase Hosting serves |
| `memory/PRD.md`, `memory/Memory.md`, etc.       | Product docs — always read Memory.md first     |

---

## 9 · Production deployment (current: EC2 + Docker)

The live app runs on a college-provided EC2 instance, not Lambda and
not your laptop. This section is for anyone maintaining that instance.

**Live URLs:**
- Frontend: `https://cec-placements23.web.app` (Firebase Hosting)
- Backend: `https://3-7-18-84.sslip.io` (EC2, Elastic IP `3.7.18.84`)

### 9.1 Why `sslip.io`

Let's Encrypt won't issue a certificate for a bare IP, and refuses
`*.amazonaws.com` hostnames by policy. `sslip.io` gives us a free,
real, resolvable domain — `3-7-18-84.sslip.io` always resolves to
`3.7.18.84` — with no signup, purely so a public CA has something
it's willing to certify. If the Elastic IP ever changes, the fix is
just re-running Certbot against the new `<ip-with-dashes>.sslip.io`,
not a rebuild.

### 9.2 SSH in and update the code

```bash
ssh -i ~/Documents/Secrets/cec_placement.pem ubuntu@3.7.18.84
cd ~/Canara-Placement-Pulse
git pull
```

### 9.3 Rebuild and restart the container

```bash
docker build -t placement-pulse-backend -f Dockerfile.backend .
docker rm -f pp-backend
docker run -d --name pp-backend --restart unless-stopped \
  -p 127.0.0.1:8001:8001 --env-file backend/.env \
  -v $(pwd)/backend/secrets:/app/backend/secrets:ro \
  placement-pulse-backend
docker logs -f pp-backend      # confirm clean startup, then Ctrl+C
```

Note `-p 127.0.0.1:8001:8001` (not `-p 8001:8001`) — the container is
deliberately only reachable from inside the instance. Nginx (already
configured, with a Let's Encrypt cert via Certbot) is what's actually
exposed on 80/443 and reverse-proxies to `127.0.0.1:8001`. The EC2
security group should only have 22, 80, and 443 open — not 8001.

Verify from outside:
```bash
curl https://3-7-18-84.sslip.io/api/health
```

### 9.4 Why EC2 and not Lambda (yet)

`lambda_handler.py`, `scripts/build-lambda-zip.sh`, and the Secrets
Manager fallback in `firebase_client.py` are all written and ready for
a serverless deployment (API Gateway + EventBridge Scheduler in place
of the in-process APScheduler). EC2 was used instead because it was
available immediately and required no IAM/Secrets Manager setup. A
Lambda migration is a real option later — see `lambda_handler.py`'s
module docstring for the target architecture — but as of this
writing, EC2 + Docker is what's actually serving students.

### 9.5 After any deploy that touches notification logic

If a change affects which notices are considered "genuinely new" for
push purposes (see `seen_slnos` handling in `ingest.py`), run one
silent ingest immediately after restarting the container, before the
next scheduled poll fires:

```bash
curl -X POST "https://3-7-18-84.sslip.io/api/admin/ingest/run?notify=false" \
  -H "X-Admin-Token: <ADMIN_TOKEN>"
```

This lets the backend "catch up" on its bookkeeping without firing a
push for entries that students have already seen.

### 9.6 Certificate renewal

Certbot already set up automatic renewal at deploy time — no action
needed under normal circumstances. If the Elastic IP is ever
re-associated to a different address, re-run:
```bash
sudo certbot --nginx -d <new-ip-with-dashes>.sslip.io
```
and update `server_name` in `/etc/nginx/sites-available/pp-backend`
to match, then `sudo nginx -t && sudo systemctl reload nginx`.

---

**When in doubt**, run `curl .../api/health` (local or production —
see §9) and read the JSON — it tells you whether Mongo is connected,
whether the scheduler is running, how many notices are in the DB, and
when the last poll finished.