"""Phase 6 backend regression tests.

Covers:
- GET /api/entries exposes ctc / branches / noticeType on each row.
- GET /api/entries supports q / company / hasRegistration / noticeType
  filters (Phase 6.5), composable with existing week / sort / cursor.
- GET /api/stats returns totalNotices/thisWeekCount/currentWeek/
  uniqueCompanies/topCompanies.
- All /api/admin/* endpoints reject requests without X-Admin-Token (401)
  and accept with the correct one.
- POST /api/admin/reprocess is idempotent and returns non-zero populations
  for ctc/branches on the live cluster; messageExcerpt populated on
  every notice (not only isMessageOnly).
- Extraction helpers behave sensibly for known inputs.
"""

import os
import re
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ---------- New fields on list endpoint ----------
class TestNewFieldsOnList:
    def test_list_carries_new_fields(self, api):
        r = api.get(f"{BASE_URL}/api/entries?limit=50")
        assert r.status_code == 200
        for e in r.json()["entries"]:
            assert "ctc" in e
            assert e["ctc"] is None or isinstance(e["ctc"], str)
            assert "branches" in e and isinstance(e["branches"], list)
            assert "noticeType" in e and isinstance(e["noticeType"], str)
            assert e["noticeType"] in {
                "drive", "internship", "results", "ppt", "notice"
            }

    def test_at_least_some_ctc_populated(self, api):
        r = api.get(f"{BASE_URL}/api/entries?limit=200")
        entries = r.json()["entries"]
        with_ctc = [e for e in entries if e.get("ctc")]
        # We already saw >= 50 across all 222 after reprocess; assert a
        # conservative lower bound so this test is stable.
        assert len(with_ctc) >= 10, f"only {len(with_ctc)} entries had ctc"

    def test_ctc_looks_like_lpa(self, api):
        r = api.get(f"{BASE_URL}/api/entries?limit=200")
        for e in r.json()["entries"]:
            if e.get("ctc"):
                assert re.match(r"^\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)? LPA$", e["ctc"]), \
                    f"unexpected CTC format: {e['ctc']!r} (slno={e['slno']})"


# ---------- /api/stats ----------
class TestStats:
    def test_stats_shape(self, api):
        r = api.get(f"{BASE_URL}/api/stats")
        assert r.status_code == 200
        d = r.json()
        for k in ("totalNotices", "thisWeekCount", "currentWeek",
                  "uniqueCompanies", "topCompanies", "latest", "lastPoll", "generatedAt"):
            assert k in d, f"missing key {k} in /api/stats response"
        assert isinstance(d["totalNotices"], int) and d["totalNotices"] > 0
        assert isinstance(d["thisWeekCount"], int) and d["thisWeekCount"] >= 0
        assert re.match(r"^\d{4}-W\d{2}$", d["currentWeek"])
        assert isinstance(d["uniqueCompanies"], int) and d["uniqueCompanies"] > 0
        # topCompanies: sorted-desc list, each {"company": str, "count": int}
        assert isinstance(d["topCompanies"], list) and len(d["topCompanies"]) > 0
        prev = None
        for row in d["topCompanies"]:
            assert isinstance(row["company"], str) and row["company"]
            assert isinstance(row["count"], int) and row["count"] >= 1
            if prev is not None:
                assert row["count"] <= prev, "topCompanies not sorted desc"
            prev = row["count"]

    def test_stats_rate_limit_header_absent(self, api):
        # sanity: /api/stats is public; hitting it 3× shouldn't 429.
        for _ in range(3):
            assert api.get(f"{BASE_URL}/api/stats").status_code == 200


# ---------- Admin auth guard ----------
ADMIN_ENDPOINTS = [
    ("GET",  "/api/admin/notices/count", None),
    ("GET",  "/api/admin/ingest/runs?limit=1", None),
    ("POST", "/api/admin/notify/mark-backfilled", None),
]


class TestAdminAuth:
    @pytest.mark.parametrize("method,path,_", ADMIN_ENDPOINTS)
    def test_admin_without_header_is_401(self, api, method, path, _):
        r = api.request(method, f"{BASE_URL}{path}")
        assert r.status_code == 401, (
            f"{method} {path} expected 401, got {r.status_code}: {r.text[:200]}"
        )

    @pytest.mark.parametrize("method,path,_", ADMIN_ENDPOINTS)
    def test_admin_with_wrong_token_is_401(self, api, method, path, _):
        r = api.request(
            method, f"{BASE_URL}{path}",
            headers={"X-Admin-Token": "not-the-real-one"},
        )
        assert r.status_code == 401

    @pytest.mark.parametrize("method,path,_", ADMIN_ENDPOINTS)
    def test_admin_with_wrong_header_name_is_401(self, api, method, path, _):
        # Old header name (from prior draft) must NOT work.
        r = api.request(
            method, f"{BASE_URL}{path}",
            headers={"X-Admin-Secret": ADMIN_TOKEN or ""},
        )
        assert r.status_code == 401

    def test_admin_notices_count_with_valid_token(self, api):
        assert ADMIN_TOKEN, "ADMIN_TOKEN env var must be set for this test"
        r = api.get(
            f"{BASE_URL}/api/admin/notices/count",
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        assert r.status_code == 200
        d = r.json()
        assert "total" in d and isinstance(d["total"], int)
        assert d["total"] > 0


# ---------- Reprocess endpoint ----------
class TestReprocess:
    def test_reprocess_updates_all_and_populates_badges(self, api):
        assert ADMIN_TOKEN
        r = api.post(
            f"{BASE_URL}/api/admin/reprocess?limit=500",
            headers={"X-Admin-Token": ADMIN_TOKEN},
            timeout=120,
        )
        assert r.status_code == 200
        d = r.json()
        assert d["processed"] > 0
        assert d["updated"] == d["processed"]
        assert d["ctcPopulated"] >= 20, f"CTC coverage {d['ctcPopulated']} too low"
        assert d["branchesPopulated"] >= 30, (
            f"branch coverage {d['branchesPopulated']} too low"
        )
        assert "typeCounts" in d and sum(d["typeCounts"].values()) == d["processed"]

    def test_message_excerpt_populated_for_all_notices(self, api):
        # Phase 6.5: excerpt now populated for every notice, not just
        # message-only ones.
        r = api.get(f"{BASE_URL}/api/entries?limit=100")
        entries = r.json()["entries"]
        assert len(entries) > 0
        empty = [e for e in entries if not (e.get("messageExcerpt") or "").strip()]
        # We tolerate a tiny handful of empties (some notices have no
        # detailsHtml at all in the raw feed), but the overwhelming
        # majority must have text.
        assert len(empty) <= 5, (
            f"{len(empty)}/{len(entries)} entries have empty messageExcerpt"
        )


# ---------- Extractor unit tests (in-process) ----------
class TestExtractors:
    def test_ctc_single_lpa(self):
        from ingest import extract_ctc
        assert extract_ctc("Package: 6.5 LPA for freshers") == "6.5 LPA"

    def test_ctc_range_lpa(self):
        from ingest import extract_ctc
        assert extract_ctc("CTC ₹8-12 LPA + benefits") == "8-12 LPA"

    def test_ctc_none_when_missing(self):
        from ingest import extract_ctc
        assert extract_ctc("A friendly reminder to all students") is None

    def test_branches_canonicalised(self):
        from ingest import extract_branches
        b = extract_branches("Open to CSE, ISE and ECE students only.")
        assert "CSE" in b and "ISE" in b and "ECE" in b

    def test_branches_all_wildcard(self):
        from ingest import extract_branches
        assert extract_branches("All branches are eligible") == ["ALL"]

    def test_notice_type_results(self):
        from ingest import extract_notice_type
        assert extract_notice_type("Radware - Final Result", "") == "results"

    def test_notice_type_internship(self):
        from ingest import extract_notice_type
        assert extract_notice_type("Codeyoung Internship Drive", "") == "internship"


# ---------- Phase 6.5 · new query params on /api/entries ---------
class TestEntriesFilters:
    def test_q_matches_company(self, api):
        r = api.get(f"{BASE_URL}/api/entries?q=codeyoung&limit=20")
        assert r.status_code == 200
        entries = r.json()["entries"]
        assert len(entries) > 0
        for e in entries:
            hay = (e["headline1"] + " " + e.get("company", "") + " " +
                   e.get("messageExcerpt", "")).lower()
            assert "codeyoung" in hay, f"slno={e['slno']} shouldn't have matched"

    def test_company_filter_partial_case_insensitive(self, api):
        r = api.get(f"{BASE_URL}/api/entries?company=codeyoung&limit=20")
        assert r.status_code == 200
        entries = r.json()["entries"]
        assert len(entries) > 0
        for e in entries:
            assert "codeyoung" in (e.get("company") or "").lower()

    def test_has_registration_true(self, api):
        r = api.get(f"{BASE_URL}/api/entries?hasRegistration=true&limit=50")
        assert r.status_code == 200
        entries = r.json()["entries"]
        assert len(entries) > 0
        for e in entries:
            assert len(e.get("registrationLinks", [])) > 0, (
                f"slno={e['slno']} slipped through with 0 registration links"
            )

    def test_has_registration_false(self, api):
        r = api.get(f"{BASE_URL}/api/entries?hasRegistration=false&limit=50")
        assert r.status_code == 200
        entries = r.json()["entries"]
        for e in entries:
            assert len(e.get("registrationLinks", [])) == 0

    def test_notice_type_composes_with_company(self, api):
        # Ensure filters compose (not mutually exclusive).
        r = api.get(
            f"{BASE_URL}/api/entries?noticeType=drive&company=codeyoung&limit=20"
        )
        assert r.status_code == 200
        entries = r.json()["entries"]
        for e in entries:
            assert e["noticeType"] == "drive"
            assert "codeyoung" in (e.get("company") or "").lower()

    def test_q_composes_with_week(self, api):
        # First find a valid week from the data.
        stats = api.get(f"{BASE_URL}/api/stats").json()
        week = stats["currentWeek"]
        r = api.get(f"{BASE_URL}/api/entries?q=code&week={week}&limit=50")
        assert r.status_code == 200
        for e in r.json()["entries"]:
            assert e["isoWeek"] == week

    def test_invalid_notice_type_rejected(self, api):
        r = api.get(f"{BASE_URL}/api/entries?noticeType=hackme")
        assert r.status_code == 400
