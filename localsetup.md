# Placement Pulse — Local Setup (VS Code)

This guide walks a new developer through cloning the repo, wiring the
credentials, installing dependencies, and running both the backend and
the PWA frontend on their laptop.

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
- ms-azuretools.vscode-docker  (only if you use the fallback Dockerfile)

---

## 2 · Clone

```bash
git clone https://github.com/sumit-chillal/Test1-of-web-app.git placement-pulse
cd placement-pulse
code .
```

Directory layout you should now see:

```
placement-pulse/
├── backend/                 FastAPI + APScheduler
│   ├── server.py
│   ├── ingest.py
│   ├── firebase_client.py
│   ├── lambda_handler.py    AWS Lambda entry (prod only)
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
└── Dockerfile.backend       EC2/container fallback image
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
MONGO_URL="mongodb+srv://<user>:<password>@<cluster>.mongodb.net/?appName=Cluster0"
DB_NAME="placement_pulse"
CORS_ORIGINS="http://localhost:3000"
COLLEGE_API_URL="https://canaraengineering.in/api/news?action=all"
POLLING_INTERVAL_SECONDS="300"
FCM_TOPIC="placement-updates"
FIREBASE_PROJECT_ID="cec-placements23"
FIREBASE_SERVICE_ACCOUNT_PATH="./secrets/firebase-admin.json"
ADMIN_TOKEN="<generate with: python -c 'import secrets;print(secrets.token_urlsafe(32))'>"

# Optional. Default "1" runs APScheduler in-process (dev + EC2).
# Set to "0" only when deploying to Lambda.
RUN_SCHEDULER_INPROC="1"
```

Notes:
- `MONGO_URL` — get it from the MongoDB Atlas UI ("Connect → Drivers").
  Add your laptop's public IP to the Atlas IP allow-list, or allow
  `0.0.0.0/0` for a dev cluster.
- `CORS_ORIGINS` — comma-separated list of every frontend origin that
  is allowed to call the API. `http://localhost:3000` for dev; add the
  Firebase Hosting URL in prod.
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

Expected: JSON with `"status":"ok"`, `mongo:"ok"`, `scheduler.running=true`,
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
REACT_APP_FIREBASE_PROJECT_ID=cec-placements23
REACT_APP_FIREBASE_STORAGE_BUCKET=cec-placements23.firebasestorage.app
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

### 4.3 Run the frontend

```bash
yarn start
```

Opens `http://localhost:3000`. It will call `REACT_APP_BACKEND_URL/api/*`.

For a production build (needed for the Lighthouse audit):

```bash
yarn build         # writes to frontend/build/
# then optionally serve it locally:
npx serve -s build -l 3001
```

---

## 5 · One-off admin operations

All `/api/admin/*` routes require the header `X-Admin-Token: <ADMIN_TOKEN>`.

```bash
TOKEN=$(grep ADMIN_TOKEN backend/.env | cut -d '=' -f2 | tr -d '"')

# Trigger one manual ingest (fetches the college API, upserts on slno)
curl -X POST -H "X-Admin-Token: $TOKEN" \
  http://localhost:8001/api/admin/ingest/run

# Backfill the Phase 6 extracted fields on already-stored notices
curl -X POST -H "X-Admin-Token: $TOKEN" \
  "http://localhost:8001/api/admin/reprocess?limit=500"

# Send a single test push (real FCM)
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
- **FCM `invalid_grant: Invalid JWT Signature`** — either the
  service-account key was rotated on the Firebase console (regenerate)
  or your machine's clock is skewed by > 5 minutes (fix NTP).
- **`yarn start` compiles but the app is blank** — check the browser
  devtools; nearly always a missing `REACT_APP_*` env var (CRA requires
  a restart of `yarn start` after changing `.env`).
- **`sudo supervisorctl` isn't available** — that's fine. It only
  exists in the Emergent hosted container. Locally you run
  `uvicorn` / `yarn start` directly.
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
| `backend/lambda_handler.py`                     | AWS Lambda entry (`api_handler`, `scheduled_ingest`) |
| `Dockerfile.backend`                            | EC2/container fallback image                    |
| `scripts/build-lambda-zip.sh`                   | Produces `pp-backend-lambda.zip` for AWS deploy |
| `frontend/.env`                                 | `REACT_APP_*` only — everything here ships in the browser bundle |
| `frontend/firebase.json`, `frontend/.firebaserc`| Firebase Hosting deploy config                  |
| `frontend/build/`                               | Output of `yarn build`; what Firebase Hosting serves |
| `memory/PRD.md`, `memory/Memory.md`, etc.       | Product docs — always read Memory.md first     |

---

**When in doubt**, run `curl http://localhost:8001/api/health` and read
the JSON — it tells you whether Mongo is connected, whether the
scheduler is running, how many notices are in the DB, and when the last
poll finished.
