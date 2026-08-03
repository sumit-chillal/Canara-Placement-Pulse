"""
Phase 3 backend tests for Placement Pulse:
  - /api/subscribe (fake token → 502 structured, never a 500)
  - /api/admin/notify/test (publish with & without slno; 404 on unknown slno)
  - build_notification content rules validated via /api/admin/notify/test
  - GET /firebase-messaging-sw.js reachable from origin root
"""
import re
import pytest


# ---------------------------------------------------------------- helpers
def _find_message_only_slno(api_client, base_url):
    r = api_client.get(f"{base_url}/api/entries?limit=200")
    assert r.status_code == 200, r.text
    for e in r.json().get("entries", []):
        if e.get("isMessageOnly"):
            return e["slno"]
    return None


# ---------------------------------------------------------------- /api/subscribe
class TestSubscribe:
    def test_subscribe_with_garbage_token_is_handled(self, api_client, base_url):
        """
        Firebase will reject a fake/garbage token but the endpoint must never
        crash with a 500 — it must return either 200 (subscribed=False) or a
        structured 502.
        """
        r = api_client.post(
            f"{base_url}/api/subscribe",
            json={"token": "TEST_garbage_token_" + "x" * 50},
        )
        assert r.status_code in (200, 502), f"unexpected {r.status_code}: {r.text}"
        body = r.json()
        if r.status_code == 200:
            # 200 path: firebase-admin accepted the request but reported errors
            assert "subscribed" in body
            assert body["topic"] == "placement-updates"
            assert "success_count" in body and "failure_count" in body
            # With a garbage token, firebase reports failure_count>=1
            # (subscribed should therefore be False)
            assert body["subscribed"] is False
        else:
            # 502 path: structured error, not a raw traceback
            assert "detail" in body
            assert "fcm_subscribe_failed" in body["detail"]

    def test_subscribe_rejects_too_short_token(self, api_client, base_url):
        r = api_client.post(f"{base_url}/api/subscribe", json={"token": "short"})
        assert r.status_code == 400
        assert "errors" in r.json()


# ---------------------------------------------------------------- /api/admin/notify/test
MSG_ID_PATTERN = re.compile(r"^projects/[^/]+/messages/.+")


class TestNotifyTest:
    def test_publish_without_slno(self, api_client, base_url, admin_headers, fcm_available):
        if not fcm_available:
            pytest.skip("FCM service-account credentials not usable in this env")
        r = api_client.post(f"{base_url}/api/admin/notify/test", headers=admin_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["published"] is True
        assert MSG_ID_PATTERN.match(body["message_id"]), body["message_id"]
        assert body["content"]["title"] and isinstance(body["content"]["title"], str)
        assert body["data"]["test"] == "1"

    def test_publish_unknown_slno_returns_404(self, api_client, base_url, admin_headers):
        # 404 fires BEFORE the FCM publish, so this is safe to run regardless
        # of whether the service-account credentials are usable.
        r = api_client.post(
            f"{base_url}/api/admin/notify/test?slno=99999999",
            headers=admin_headers,
        )
        assert r.status_code == 404
        assert "not found" in r.json().get("detail", "").lower()

    def test_publish_with_slno_10775_no_fake_deadline(self, api_client, base_url, admin_headers, fcm_available):
        """
        slno=10775 is 'Storeys Real Estate' Results — no deadline present
        → body MUST NOT claim 'Registration closes on'.
        Also validates the deep-link data payload (slno) is echoed back.
        """
        if not fcm_available:
            pytest.skip("FCM service-account credentials not usable in this env")
        r = api_client.post(
            f"{base_url}/api/admin/notify/test?slno=10775",
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["published"] is True
        assert MSG_ID_PATTERN.match(body["message_id"])
        assert body["content"]["title"] == "Storeys Real Estate"
        assert "Registration closes on" not in body["content"]["body"]
        assert str(body["data"]["slno"]) == "10775"
        assert body["data"]["test"] == "1"
        # Phase 6 · data payload also carries company + url deep-link
        assert body["data"].get("url") == "/entries/10775"
        assert "company" in body["data"]
        # Phase 6 · data.deeplink must also be present (spec update)
        assert body["data"].get("deeplink") == "/entries/10775"

    def test_publish_with_slno_10772_has_deadline(self, api_client, base_url, admin_headers, fcm_available):
        """
        slno=10772 is a Codeyoung Recruitment Drive with 'Register by 17th
        July, 3.00PM' in the headline → body MUST contain
        'Registration closes on'.
        """
        if not fcm_available:
            pytest.skip("FCM service-account credentials not usable in this env")
        r = api_client.post(
            f"{base_url}/api/admin/notify/test?slno=10772",
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["published"] is True
        assert "Registration closes on" in body["content"]["body"], body["content"]

    def test_publish_message_only_notice(self, api_client, base_url, admin_headers, fcm_available):
        """
        isMessageOnly → title MUST equal 'Placement Department' and body
        is non-empty plain text.
        """
        if not fcm_available:
            pytest.skip("FCM service-account credentials not usable in this env")
        slno = _find_message_only_slno(api_client, base_url)
        if slno is None:
            pytest.skip("No isMessageOnly notice available to exercise this rule")
        r = api_client.post(
            f"{base_url}/api/admin/notify/test?slno={slno}",
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["content"]["title"] == "Placement Department"
        assert isinstance(body["content"]["body"], str)
        assert len(body["content"]["body"]) > 0
        # And no HTML tags should have survived the plain-text conversion.
        assert "<" not in body["content"]["body"] and ">" not in body["content"]["body"]


# ---------------------------------------------------------------- service worker
class TestServiceWorker:
    def test_sw_reachable_at_origin_root(self, api_client, base_url):
        # This asset is served by the frontend (port 3000). Only fetch it
        # when we're actually running against a URL that has both the
        # frontend and the backend behind it (i.e. the ingress preview
        # URL). Skip when tests point at localhost:8001.
        if "localhost" in base_url or "127.0.0.1" in base_url:
            pytest.skip("SW served by frontend; localhost:8001 is backend-only")
        r = api_client.get(f"{base_url}/firebase-messaging-sw.js")
        assert r.status_code == 200
        ctype = r.headers.get("content-type", "").lower()
        assert "javascript" in ctype, f"unexpected CT {ctype}"
        # Sanity check: file contains the SW contract we ship
        assert "onBackgroundMessage" in r.text
        assert "notificationclick" in r.text
