import os
import pytest
import requests

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://notify-drive.preview.emergentagent.com",
).rstrip("/")

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture
def api_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture
def admin_headers():
    """Auth header for /api/admin/* routes (Phase 6)."""
    return {"X-Admin-Token": ADMIN_TOKEN}


@pytest.fixture(scope="session")
def fcm_available():
    """
    Session probe. Tries a single /api/admin/notify/test (no slno). If it
    502s on invalid_grant / JWT (service-account key rejected by Google,
    or container clock skewed past Google's tolerance), we mark FCM
    unavailable and downstream publish tests skip themselves.
    """
    if not ADMIN_TOKEN:
        return False
    try:
        r = requests.post(
            f"{BASE_URL}/api/admin/notify/test",
            headers={"X-Admin-Token": ADMIN_TOKEN, "Content-Type": "application/json"},
            timeout=15,
        )
    except requests.RequestException:
        return False
    if r.status_code == 200:
        return True
    body = r.text.lower()
    if r.status_code == 502 and (
        "invalid_grant" in body or "jwt" in body or "unauthenticated" in body
    ):
        return False
    return False
