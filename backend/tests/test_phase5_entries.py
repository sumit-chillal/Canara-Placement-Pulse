"""Phase 5 backend regression tests.

Covers:
- GET /api/entries excludes detailsHtml but includes new fields (closingDate, messageExcerpt).
- GET /api/entries/{slno} still includes detailsHtml.
- closingDate correctness (10775 = None, 10772 = non-null containing "17" or "July").
- >= 40 current-year entries have closingDate populated.
- Smart heuristic labels: >= 5 entries with links labeled from {JD, Eligibility, Apply,
  Results, Circular, Presentation}, plus slno 10773 has both JD and Eligibility.
"""

import os
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
SMART_LABELS = {"JD", "Eligibility", "Apply", "Results", "Circular", "Presentation"}


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ---------- List endpoint schema ----------
class TestEntriesList:
    def test_list_status_and_shape(self, api):
        r = api.get(f"{BASE_URL}/api/entries?limit=5")
        assert r.status_code == 200
        d = r.json()
        assert "entries" in d and isinstance(d["entries"], list)
        assert len(d["entries"]) > 0

    def test_list_excludes_detailsHtml(self, api):
        r = api.get(f"{BASE_URL}/api/entries?limit=50")
        assert r.status_code == 200
        for e in r.json()["entries"]:
            assert "detailsHtml" not in e, (
                f"detailsHtml must be projected out of list responses (slno={e.get('slno')})"
            )

    def test_list_has_new_fields(self, api):
        r = api.get(f"{BASE_URL}/api/entries?limit=50")
        for e in r.json()["entries"]:
            # closingDate: string OR null
            assert "closingDate" in e
            assert e["closingDate"] is None or isinstance(e["closingDate"], str)
            # messageExcerpt: string, non-empty for isMessageOnly, empty otherwise
            assert "messageExcerpt" in e
            assert isinstance(e["messageExcerpt"], str)
            if e.get("isMessageOnly"):
                assert e["messageExcerpt"] != "", (
                    f"isMessageOnly entry {e['slno']} has empty messageExcerpt"
                )
            # existing required fields
            for k in ("slno", "headline1", "date", "company", "isMessageOnly",
                      "isoWeek", "registrationLinks"):
                assert k in e, f"missing {k} on slno={e.get('slno')}"


# ---------- Detail endpoint ----------
class TestEntryDetail:
    def test_detail_10775_has_detailsHtml(self, api):
        r = api.get(f"{BASE_URL}/api/entries/10775")
        assert r.status_code == 200
        d = r.json()
        assert "detailsHtml" in d
        assert isinstance(d["detailsHtml"], str) and len(d["detailsHtml"]) > 0

    def test_10775_closingDate_is_null(self, api):
        r = api.get(f"{BASE_URL}/api/entries/10775")
        d = r.json()
        assert d.get("closingDate") is None, (
            f"10775 (Results notice) must have null closingDate, got {d.get('closingDate')!r}"
        )

    def test_10772_closingDate_contains_17_or_july(self, api):
        r = api.get(f"{BASE_URL}/api/entries/10772")
        d = r.json()
        cd = d.get("closingDate")
        assert cd is not None, "10772 must have a non-null closingDate"
        assert isinstance(cd, str)
        assert ("17" in cd) or ("July" in cd) or ("july" in cd.lower()), (
            f"10772 closingDate={cd!r} should contain '17' or 'July'"
        )


# ---------- Coverage of closingDate extraction ----------
class TestClosingDateCoverage:
    def test_at_least_40_entries_have_closingDate(self, api):
        # Paginate through all entries
        all_entries = []
        cursor = None
        for _ in range(20):
            params = {"limit": 200}
            if cursor:
                params["cursor"] = cursor
            r = api.get(f"{BASE_URL}/api/entries", params=params)
            assert r.status_code == 200
            d = r.json()
            all_entries.extend(d.get("entries", []))
            cursor = d.get("next_cursor")
            if not cursor:
                break
        assert len(all_entries) > 40, f"only {len(all_entries)} entries returned"
        with_cd = [e for e in all_entries if e.get("closingDate")]
        assert len(with_cd) >= 40, (
            f"only {len(with_cd)} entries have non-null closingDate; expected >= 40"
        )


# ---------- Smart link labels ----------
class TestSmartLinkLabels:
    def test_10773_has_jd_and_eligibility(self, api):
        r = api.get(f"{BASE_URL}/api/entries/10773")
        assert r.status_code == 200
        labels = {l.get("label") for l in r.json().get("registrationLinks", [])}
        assert "JD" in labels, f"expected 'JD' in {labels}"
        assert "Eligibility" in labels, f"expected 'Eligibility' in {labels}"

    def test_at_least_5_entries_use_smart_labels(self, api):
        all_entries = []
        cursor = None
        for _ in range(20):
            params = {"limit": 200}
            if cursor:
                params["cursor"] = cursor
            r = api.get(f"{BASE_URL}/api/entries", params=params)
            d = r.json()
            all_entries.extend(d.get("entries", []))
            cursor = d.get("next_cursor")
            if not cursor:
                break
        matches = 0
        for e in all_entries:
            for l in e.get("registrationLinks", []) or []:
                if l.get("label") in SMART_LABELS:
                    matches += 1
                    break
        assert matches >= 5, (
            f"only {matches} entries have a smart-label link; expected >= 5"
        )
