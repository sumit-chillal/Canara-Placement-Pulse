"""Placement Pulse — ingestion pipeline.

One entry point: `run_ingest(db)`.

  fetch  ─►  filter to current IST year  ─►  normalize / extract
  ─►  upsert into `notices` keyed by slno
  ─►  return (& persist) an ingest_run audit doc

Called by:
  · APScheduler on a day/night cadence (see server.py)
  · Manual POST /api/admin/ingest/run
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, quote, urlparse
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from motor.motor_asyncio import AsyncIOMotorDatabase

from firebase_client import publish_to_topic
from discord_notify import notify_discord

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger("placement-pulse.ingest")

# Phase 8 · hard cap on total stored notices. Every ingest run trims the
# collection back down to this many, deleting the oldest overflow first
# (by date, slno as tiebreaker) — so Mongo storage never grows past a
# fixed, predictable ceiling regardless of how long the poller runs.
NOTICE_CAP = 100

# Where bare (relative) attachment filenames from the college API live.
_FILE_BASE_URL = "https://canaraengineering.in/news_notice/"
_COLLEGE_HOST = "canaraengineering.in"

# Trailing junk we strip from headline1 when guessing a company name.
# Matches EITHER  "<sep> <status-phrase>"  OR bare trailing corp phrases.
_HEADLINE_TRIM = re.compile(
    r"\s*(?:"
    # separator-prefixed status phrases
    r"(?:\|\||[-|:•])\s*"
    r"(?:final\s+result[s]?|results?|shortlist(?:ed)?.*|"
    r"selected\s+candidates?.*|"
    r"pre[-\s]*placement\s+talk.*|ppt.*|"
    r"registration.*|apply\s+link.*|walk[-\s]*in.*|"
    r"campus\s+recruitment\s+drive.*|campus\s+placement\s+drive.*|"
    r"recruitment\s+drive.*|placement\s+drive.*|hiring\s+drive.*)"
    r"|"
    # bare trailing corporate phrases (no separator required)
    r"(?:'s)?\s+(?:campus\s+recruitment\s+drive|campus\s+placement\s+drive|"
    r"recruitment\s+drive|placement\s+drive|hiring\s+drive|"
    r"pre[-\s]*placement\s+talk|walk[-\s]*in\s+drive).*"
    r")\s*$",
    re.IGNORECASE,
)


# --------------------------------------------------------------------- #
#  Small pure helpers
# --------------------------------------------------------------------- #
def _parse_date(s: str) -> datetime | None:
    try:
        return datetime.strptime((s or "").strip(), "%d/%m/%Y").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def iso_week_of(dt: datetime) -> str:
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _clean(values: list[Any]) -> list[str]:
    out = []
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s and s != "_":
            out.append(s)
    return out


# Exact (case-insensitive, trimmed) phrases seen coming through the
# link-text fallback in _extract_company that are clearly NOT company
# names — generic form/notice boilerplate rather than an actual org.
_COMPANY_LINK_BLOCKLIST = {
    "apply", "apply now", "application form", "campus", "invitation",
    "invitation for", "invitation for graduates",
    "request to participate", "request to participate in",
    "click here", "read more", "download", "notice", "circular",
    "register", "registration", "form", "attachment", "here",
}

# Anything that looks like an email address or a URL/bare domain is
# never a company name, regardless of the blocklist above.
_EMAIL_OR_URL_RE = re.compile(
    r"@|https?://|www\.|\.[a-z]{2,4}(?:/|$)", re.IGNORECASE
)


def _looks_like_company_name(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 3 or len(t) > 60:
        return False
    if _EMAIL_OR_URL_RE.search(t):
        return False
    if t.lower() in _COMPANY_LINK_BLOCKLIST:
        return False
    # Must contain a real run of letters — rejects stray symbols/IDs
    # that technically pass the length check but aren't a name.
    if not re.search(r"[A-Za-z]{3,}", t):
        return False
    return True


def _extract_company(entry: dict, details_html: str) -> str:
    # The college API's OWN "company" field is not curated — it's
    # frequently an email address, a raw form URL, or generic
    # boilerplate text rather than an actual company name (that's
    # exactly what was still showing up after the previous fix, which
    # only validated the link-scan fallback below and let this path
    # return anything unchecked). Validate it the same way as every
    # other candidate.
    raw = (entry.get("company") or "").strip()
    if raw and _looks_like_company_name(raw):
        return raw

    headline = (entry.get("headline1") or "").strip()
    if headline:
        trimmed = _HEADLINE_TRIM.sub("", headline).strip(" -|:•\t")
        # Only trust the trim if it actually removed something meaningful
        if (
            trimmed
            and trimmed != headline
            and len(trimmed) >= 3
            and _looks_like_company_name(trimmed)
        ):
            return trimmed

    # Fall back to the first meaningful <a> text inside the details HTML
    # — but only if it actually looks like a company name. Email
    # addresses, bare URLs, and generic form/notice phrases (very
    # common as the first link's visible text) are explicitly rejected
    # rather than displayed as if they were the hiring company.
    try:
        soup = BeautifulSoup(details_html or "", "html.parser")
        for a in soup.find_all("a"):
            text = (a.get_text() or "").strip()
            if _looks_like_company_name(text):
                return text
    except Exception:  # noqa: BLE001 — parser must never break ingest
        pass

    return ""


_FILE_LABEL_RULES = [
    (re.compile(r"\bjd\b|job[_\-]?description", re.I), "JD"),
    (re.compile(r"eligibility|criteria|shortlist", re.I), "Eligibility"),
    (re.compile(r"result|selected|final[_\-]?list", re.I), "Results"),
    # Meeting-platform links checked before the generic "apply" pattern
    # below, since a Teams/Zoom/Meet URL often also contains words like
    # "join" that could otherwise be mis-bucketed.
    (re.compile(r"zoom\.us|teams\.microsoft\.com|meet\.google\.com|"
                r"webex\.com|/meet/|join\.skype\.com", re.I), "Meeting Link"),
    (re.compile(r"apply|register|form|link", re.I), "Apply"),
    (re.compile(r"circular|notice|announcement", re.I), "Circular"),
    (re.compile(r"presentation|slides|\bppt\b", re.I), "Presentation"),
    (re.compile(r"schedule|timing|agenda", re.I), "Schedule"),
    (re.compile(r"brochure|poster", re.I), "Brochure"),
]


def _label_for_file(url: str, fallback: str) -> str:
    for pat, label in _FILE_LABEL_RULES:
        if pat.search(url):
            return label
    return fallback


# Text that IS, by itself, a bare phone number: digits with optional
# +, spaces, hyphens, dots, parens — e.g. "8217612080", "+91 8217612080".
# Deliberately requires the WHOLE string to look like a phone number
# (not just "contains digits") so this never accidentally swallows a
# real label like "JD-2027" or "Round 2 Results".
_PHONE_ONLY_RE = re.compile(r"^[+()\-.\s\d]{7,20}$")


def _is_personal_contact_text(text: str) -> bool:
    """
    True when `text`, by itself, IS a bare email address or phone
    number — e.g. a "Selected Students" results notice that lists
    each student's phone number as the visible text of their own
    entry. Used both for anchor text (see _is_personal_contact_link)
    and for the college portal's own *_file label field, since either
    could carry this without an obviously personal-looking href.
    """
    t = (text or "").strip()
    if not t:
        return False
    if "@" in t:
        return True
    digits = re.sub(r"\D", "", t)
    return bool(_PHONE_ONLY_RE.match(t) and 7 <= len(digits) <= 15)


def _is_personal_contact_link(href: str, text: str) -> bool:
    """
    True for mailto:/tel: links, or anchors whose visible text is
    itself an email address or a bare phone number — these are
    virtually always a candidate's or student's personal contact info
    embedded in a results/shortlist table (e.g. a list of selected
    students' phone numbers, each individually hyperlinked), never a
    real apply/registration destination. Excluded entirely (not just
    relabeled) both to keep cards clean and because publishing a
    student's personal phone number or email as a clickable button is
    a real privacy concern, not just a cosmetic one.
    """
    h = href.strip().lower()
    if h.startswith("mailto:") or h.startswith("tel:"):
        return True
    return _is_personal_contact_text(text)


# Bare domain/path text with NO protocol at all, e.g.
# "test.aaptor.com/forms/public/AJxzOKE6uFsD-fwkv6UPBQ". Notices are
# often pasted as plain text without "http(s)://", so checking only
# for the http(s)/www prefixes (as before) let these slip through as
# if they were real human labels.
_BARE_URL_RE = re.compile(r"^\S+\.[a-z]{2,}(?:/\S*)?$", re.IGNORECASE)


def _is_url_like(text: str) -> bool:
    """
    True when anchor text is empty or is itself a URL — i.e. NOT a real
    human label. Used to stop raw hrefs from ever becoming button text
    on a card (the bug behind links rendering as full URLs instead of
    'Apply'/'Register' style buttons). Catches both protocol-prefixed
    URLs (http://, https://, www.) AND bare domain/path text with no
    protocol prefix at all.
    """
    t = (text or "").strip()
    if not t:
        return True
    tl = t.lower()
    if tl.startswith(("http://", "https://", "www.")):
        return True
    # Bare domain — no spaces, and matches "word.tld[/path]".
    if " " not in t and _BARE_URL_RE.match(t):
        return True
    return False


def _resolve_link(raw: str) -> dict | None:
    """
    Resolve a raw attachment/link value from the college API into a
    stored link record: {"sourceUrl": ..., "hostedByCollege": bool}.

    - Bare filenames (most attachments, e.g. "JD_JD - EK27.pdf") are
      resolved against the known news_notice/ directory on the college
      site and marked hostedByCollege=True — these get proxied through
      OUR backend at serve time (Architecture Rule #2: the browser must
      never talk to canaraengineering.in directly).
    - Already-absolute URLs pointing at canaraengineering.in are also
      marked hostedByCollege=True (same proxy treatment).
    - Already-absolute URLs pointing anywhere else (external company
      application forms, career pages, Google Drive links, etc.) are
      left exactly as-is and marked hostedByCollege=False — these are
      NOT the college's own infrastructure, so a direct link is fine,
      and proxying them would break forms/JS/cookies on the external
      site.
    """
    raw = (raw or "").strip()
    if not raw:
        return None

    if re.match(r"^https?://", raw, re.IGNORECASE):
        host = urlparse(raw).netloc.lower()
        hosted = _COLLEGE_HOST in host
        return {"sourceUrl": raw, "hostedByCollege": hosted}

    # Bare filename — always a college-hosted attachment.
    absolute = urljoin(_FILE_BASE_URL, quote(raw, safe="%/:?=&"))
    return {"sourceUrl": absolute, "hostedByCollege": True}


def _finalize_links(links: list[dict]) -> list[dict]:
    """
    Post-process the raw link list before it's persisted:

      - Drop exact duplicate destinations (same sourceUrl), keeping the
        first occurrence — the same URL surfacing twice in a notice
        body (e.g. both a "Registration link:" line and an
        "Attachments & links" section pointing at the same form) should
        render as ONE button, not two.
      - Number same-label duplicates: several attachments that all
        resolve to the label "JD" become "JD 1", "JD 2", "JD 3", ...
        instead of several indistinguishable identical buttons. Labels
        that are already unique are left untouched.

    Order-preserving in both steps.
    """
    seen_urls: set[str] = set()
    deduped: list[dict] = []
    for link in links:
        url = link.get("sourceUrl")
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        deduped.append(link)

    counts: dict[str, int] = {}
    for link in deduped:
        counts[link["label"]] = counts.get(link["label"], 0) + 1

    seen_n: dict[str, int] = {}
    out: list[dict] = []
    for link in deduped:
        label = link["label"]
        if counts[label] > 1:
            seen_n[label] = seen_n.get(label, 0) + 1
            link = {**link, "label": f"{label} {seen_n[label]}"}
        out.append(link)
    return out


def _extract_links(entry: dict, details_html: str) -> list[dict]:
    """
    The college API stores each attachment as a PAIR:
      upload{N}       = filename / URL
      {ord}_file      = display label (first_file, second_file, ...)
    We zip them together. If only one half is present we still emit a
    single link. `apply_but` is the standalone Apply CTA. Falls back to
    parsing <a href> tags out of details_html if nothing structured
    exists.

    Each link is {"label", "sourceUrl", "hostedByCollege"}. The public
    API layer (server.py `_serialize`) turns this into the client-facing
    {"label", "url"} shape — proxied through /api/files/... when
    hostedByCollege, passed through untouched otherwise.

    NOTE: labels are NEVER allowed to be a raw URL. Every branch below
    (structured fields, and the <a href> fallback) runs through a
    label heuristic with a sane human-readable fallback ("Apply" /
    "Link N"), never the href/URL text itself. The final list is also
    run through `_finalize_links` to drop duplicate destinations and
    number repeated labels (e.g. several "JD" attachments -> "JD 1",
    "JD 2", ...) before being returned.
    """
    links: list[dict] = []

    apply_v = (entry.get("apply_but") or "").strip()
    if apply_v and apply_v != "_":
        resolved = _resolve_link(apply_v)
        if resolved:
            links.append({"label": "Apply", **resolved})

    upload_keys = ("upload1", "upload2", "upload3", "upload4", "upload5")
    file_keys = (
        "first_file", "second_file", "third_file", "fourth_file", "fifth_file",
    )
    for i, (uk, fk) in enumerate(zip(upload_keys, file_keys), start=1):
        u = (entry.get(uk) or "").strip()
        f = (entry.get(fk) or "").strip()
        u_ok = u and u != "_"
        f_ok = f and f != "_"
        if not (u_ok or f_ok):
            continue
        raw_val = u if u_ok else f
        # The college portal's *_file field (first_file, second_file, ...)
        # IS the human label an admin actually typed for this attachment
        # on the official site — e.g. "Registered Students List" or
        # "Campus copy of Company profile- Codeyoung (1)". Use it
        # VERBATIM whenever it's present and isn't itself a bare
        # filename/URL, so our card matches the official portal exactly
        # instead of silently re-guessing a (sometimes wrong) label from
        # keywords. "Registered Students List" contains the substring
        # "regist", which used to false-match the "apply|register|..."
        # heuristic and render as "Apply" — that's exactly the bug this
        # avoids. Only fall back to keyword heuristics when there's no
        # usable human label at all (missing, or itself URL-like).
        if f_ok and not _is_url_like(f) and not _is_personal_contact_text(f):
            label = f
        else:
            candidate = f if f_ok else u
            label = _label_for_file(candidate, f"Attachment {i}")
        resolved = _resolve_link(raw_val)
        if resolved:
            links.append({"label": label, **resolved})

    if links:
        return _finalize_links(links)

    try:
        soup = BeautifulSoup(details_html or "", "html.parser")
        anchors = [
            a for a in soup.find_all("a", href=True)
            if a["href"].strip() and not _is_personal_contact_link(a["href"].strip(), a.get_text() or "")
        ]
        for a in anchors:
            href = a["href"].strip()
            text = (a.get_text() or "").strip()
            if _is_url_like(text):
                # Anchor text is empty or is itself a URL — never show a
                # raw link as the button label. Derive a real label from
                # the href using the same heuristics as file attachments.
                # A single unlabeled link is almost always the
                # apply/registration destination, so that one case still
                # defaults to "Apply"; with several unlabeled links the
                # ambiguity is too high (that's exactly the shortlist-
                # table-full-of-links scenario), so those are dropped
                # rather than shown as a meaningless "Link N".
                fallback = "Apply" if len(anchors) == 1 else ""
                label = _label_for_file(href, fallback)
                if not label:
                    continue
                text = label
            resolved = _resolve_link(href)
            if resolved:
                links.append({"label": text, **resolved})
    except Exception:  # noqa: BLE001
        pass

    return _finalize_links(links)


# --------------------------------------------------------------------- #
#  Closing-date + notification builder
# --------------------------------------------------------------------- #
_CLOSING_PATTERNS = [
    re.compile(
        r"register(?:ing|ration)?\s*(?:by|before)\s*[:\-]?\s*([^,.\n<|]{3,30})",
        re.IGNORECASE,
    ),
    re.compile(
        r"registration\s+(?:will\s+)?closes?\s+(?:on\s+)?([^,.\n<|]{3,30})",
        re.IGNORECASE,
    ),
    re.compile(
        r"last\s+date(?:\s+to\s+register)?\s*[:\-]?\s*([^,.\n<|]{3,30})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:deadline|closes?\s+on|close\s+on)\s*[:\-]?\s*([^,.\n<|]{3,30})",
        re.IGNORECASE,
    ),
    re.compile(
        r"apply\s+by\s+([^,.\n<|]{3,30})",
        re.IGNORECASE,
    ),
]

_DEADLINE_TRIM = re.compile(
    r"\s+(?:placement\s+department|please\s+find|regards?).*$",
    re.IGNORECASE,
)


def _plain_text(html_str: str, maxlen: int | None = None) -> str:
    try:
        text = BeautifulSoup(html_str or "", "html.parser").get_text(" ")
    except Exception:  # noqa: BLE001
        text = html_str or ""
    text = " ".join(text.split())
    if maxlen and len(text) > maxlen:
        text = text[: maxlen - 1].rstrip() + "…"
    return text


def extract_closing_date(notice: dict) -> str | None:
    """
    Best-effort deadline extraction from headline + detailsHtml plain text.
    Returns None when no confident match — the notification then falls
    back to 'company name only' with no fake deadline (spec requirement).
    """
    haystack = "  ".join([
        notice.get("headline1") or "",
        _plain_text(notice.get("detailsHtml") or ""),
    ])
    for pat in _CLOSING_PATTERNS:
        m = pat.search(haystack)
        if not m:
            continue
        raw = m.group(1).strip(" :\t-|")
        raw = _DEADLINE_TRIM.sub("", raw).strip()
        if len(raw) >= 3:
            return raw
    return None


# --------------------------------------------------------------------- #
#  Phase 6 · CTC / branches / notice-type extraction (at ingest time).
#  Kept deliberately conservative — a missed extraction is better than a
#  wrong badge on a placement card students trust.
# --------------------------------------------------------------------- #
_CTC_PATTERNS = [
    # ranges: "6-8 LPA", "6 to 8 LPA", "₹6-8 LPA"
    re.compile(
        r"(?:₹|Rs\.?|INR)?\s*(\d{1,2}(?:\.\d{1,2})?)\s*(?:-|to|–|—)\s*"
        r"(\d{1,2}(?:\.\d{1,2})?)\s*(?:LPA|L\s*P\s*A|Lakh|Lac|LacS|LPa)\b",
        re.IGNORECASE,
    ),
    # single: "6 LPA", "6.5 LPA"
    re.compile(
        r"(?:₹|Rs\.?|INR)?\s*(\d{1,2}(?:\.\d{1,2})?)\s*(?:LPA|L\s*P\s*A|Lakh|Lac|LacS|LPa)\b",
        re.IGNORECASE,
    ),
    # "CTC: 6,00,000" style
    re.compile(
        r"(?:CTC|Package|Salary|Stipend)\s*[:\-]?\s*(?:₹|Rs\.?|INR)?\s*"
        r"([\d,]{4,})",
        re.IGNORECASE,
    ),
]

_BRANCH_ALIASES = [
    ("AIML", ["ai\\s*&?\\s*ml", "artificial\\s+intelligence\\s+(?:and|&)?\\s*machine\\s+learning", "ai[/\\-\\s]?ml", "\\baiml\\b"]),
    ("CSE",  ["\\bcse\\b", "computer\\s+science(?:\\s+(?:and|&)\\s+engineering)?", "\\bcs\\s+(?:engg?|engineering)"]),
    ("ISE",  ["\\bise\\b", "information\\s+science(?:\\s+(?:and|&)\\s+engineering)?"]),
    ("IT",   ["\\bit\\b(?!\\s*al)", "information\\s+technology"]),
    ("ECE",  ["\\bece\\b", "electronics?(?:\\s+(?:and|&)\\s+communication)?"]),
    ("EEE",  ["\\beee\\b", "electrical(?:\\s+(?:and|&)\\s+electronics)?"]),
    ("ME",   ["\\bme\\b(?!\\s*ch)", "mechanical\\s+engineering"]),
    ("CV",   ["\\bcv\\b", "civil\\s+engineering"]),
    ("DS",   ["\\bds\\b", "data\\s+science"]),
    ("MBA",  ["\\bmba\\b"]),
    ("MCA",  ["\\bmca\\b"]),
]
_BRANCH_COMPILED = [
    (code, re.compile("|".join(pats), re.IGNORECASE))
    for code, pats in _BRANCH_ALIASES
]

# Common "all branches" phrasing → treat as sentinel so UI can render one chip.
_ALL_BRANCHES_RE = re.compile(
    r"\ball\s+(?:branches|streams|departments|eligible\s+branches)\b",
    re.IGNORECASE,
)

_TYPE_RULES = [
    ("results",    re.compile(r"\b(result[s]?|final\s+result|selected|shortlist(?:ed)?)\b", re.I)),
    ("ppt",        re.compile(r"\b(pre[-\s]*placement\s+talk|\bppt\b)\b", re.I)),
    ("internship", re.compile(r"\b(intern(?:ship)?|summer\s+intern)\b", re.I)),
    ("drive",      re.compile(r"\b(campus\s+(?:recruitment|placement)\s+drive|placement\s+drive|recruitment\s+drive|hiring\s+drive|walk[-\s]*in)\b", re.I)),
]


def _fmt_ctc_amount(x: str) -> str:
    """Pretty-print a numeric-with-optional-decimal string."""
    x = x.strip()
    if "." in x:
        # trim trailing zero fraction: "6.00" -> "6"
        val = float(x)
        if val.is_integer():
            return str(int(val))
        return f"{val:g}"
    return x


def extract_ctc(text: str) -> str | None:
    """Best-effort CTC/stipend extractor. Returns e.g. '6-8 LPA' or None."""
    if not text:
        return None
    haystack = " ".join(text.split())

    m = _CTC_PATTERNS[0].search(haystack)
    if m:
        lo, hi = _fmt_ctc_amount(m.group(1)), _fmt_ctc_amount(m.group(2))
        return f"{lo}-{hi} LPA"

    m = _CTC_PATTERNS[1].search(haystack)
    if m:
        return f"{_fmt_ctc_amount(m.group(1))} LPA"

    m = _CTC_PATTERNS[2].search(haystack)
    if m:
        raw = m.group(1).replace(",", "")
        try:
            n = int(raw)
        except ValueError:
            return None
        if n >= 100000:
            lpa = n / 100000
            return f"{lpa:g} LPA"
        # too small to be CTC — likely a stipend in ₹ / month; skip.
    return None


def extract_branches(text: str) -> list[str]:
    """Return canonical branch codes present in the text, order-preserved."""
    if not text:
        return []
    if _ALL_BRANCHES_RE.search(text):
        return ["ALL"]
    found = []
    for code, pat in _BRANCH_COMPILED:
        if pat.search(text):
            found.append(code)
    return found


def extract_notice_type(headline: str, plain_details: str) -> str:
    """
    Classify a notice for UI badging. Precedence intentional: results >
    ppt > internship > drive > notice. Anything not matched is 'notice'.
    """
    hay = f"{headline}  {plain_details}"
    for label, pat in _TYPE_RULES:
        if pat.search(hay):
            return label
    return "notice"


def build_notification(notice: dict) -> dict:
    """
    Build {title, body} per the Phase 3 content rules:
      - isMessageOnly → plain message text (no fake company/deadline).
      - company + detected deadline → title=company, body="Registration closes on …".
      - company only → title=company, body=headline (no fake deadline).
      - neither → title=headline (fallback).
    """
    headline = (notice.get("headline1") or "").strip()

    if notice.get("isMessageOnly"):
        body = _plain_text(notice.get("detailsHtml") or "", maxlen=140)
        return {
            "title": "Placement Department",
            "body": body or headline or "New notice",
        }

    company = (notice.get("company") or "").strip()
    deadline = extract_closing_date(notice)

    if company and deadline:
        return {
            "title": company,
            "body": f"Registration closes on {deadline}",
        }
    if company:
        return {"title": company, "body": headline}
    return {"title": headline or "New placement notice", "body": ""}


def _target_for_notice(notice: dict, base_topic: str) -> tuple[str | None, str | None]:
    """
    Returns (topic, condition) — exactly one is non-None, matching the
    two mutually-exclusive params publish_to_topic() accepts.

    - No branches extracted, or branches includes the "ALL" sentinel:
      target the plain base topic — every subscriber gets it,
      regardless of their branch preference.
    - Specific branches (e.g. ["CSE", "ISE"]): target an FCM condition
      matching ANY of those branch topics. Using a condition (rather
      than publishing separately to each branch topic) is what keeps
      a student subscribed to multiple branches from getting the same
      notice twice — a condition-matched send reaches a device exactly
      once no matter how many of the OR'd topics it's subscribed to.
      FCM conditions support up to 5 topics; extraction already caps
      branches at a handful per notice in practice.
    """
    branches = notice.get("branches") or []
    if not branches or "ALL" in branches:
        return base_topic, None
    codes = branches[:5]
    condition = " || ".join(f"'{base_topic}-branch-{b}' in topics" for b in codes)
    return None, condition


async def _publish_new_notices(new_notices: list[dict], topic: str) -> dict:
    """
    Publish one push per genuinely-new notice — every single one gets
    its own individual notification with the real company name.

    This used to collapse anything over MAX_INDIVIDUAL_PUSHES_PER_RUN
    (5) new notices in one poll into a single generic "N new drives"
    summary instead of real per-company pushes. That's been removed
    per explicit product requirement: every new notice must be pushed
    individually after every ingest cycle, with no batching exception
    for bursts. This is safe now that notification eligibility is
    tracked via the permanent `seen_slnos` record (see run_ingest) —
    a burst of dozens of "new" entries from a cap-eviction recycle or
    a stale first-run backfill can no longer happen, since those are
    exactly what `seen_slnos` filters out before this function is ever
    called. Anything reaching here is genuinely new and should reach
    students as its own notification.

    Returns `published_slnos` — the slnos that were confirmed
    successfully published — so the caller can stamp `notifiedAt`
    precisely on those, rather than assuming successes are a prefix
    of the input list (which isn't guaranteed when individual
    publishes can fail independently of each other).
    """
    if not new_notices:
        return {"published": 0, "failed": 0, "batched": False,
                "message_ids": [], "published_slnos": [], "errors": []}

    published = 0
    failed = 0
    message_ids: list[str] = []
    published_slnos: list[int] = []
    errors: list[str] = []

    for notice in new_notices:
        content = build_notification(notice)
        target_topic, target_condition = _target_for_notice(notice, topic)
        try:
            mid = publish_to_topic(
                target_topic,
                condition=target_condition,
                title=content["title"],
                body=content["body"],
                data={
                    "slno": notice["slno"],
                    "company": notice.get("company") or "",
                    "deeplink": f"/entries/{notice['slno']}",
                    "url": f"/entries/{notice['slno']}",
                },
            )
            message_ids.append(mid)
            published += 1
            published_slnos.append(notice["slno"])
        except Exception as exc:  # noqa: BLE001
            failed += 1
            errors.append(f"slno={notice['slno']}: {type(exc).__name__}: {exc}")

    return {
        "published": published,
        "failed": failed,
        "batched": False,
        "message_ids": message_ids,
        "published_slnos": published_slnos,
        "errors": errors,
    }


async def _enforce_notice_cap(db: AsyncIOMotorDatabase, limit: int = NOTICE_CAP) -> int:
    """
    Keep only the `limit` most recent notices. "Most recent" uses the
    same ordering as the public 'latest' sort (date desc, slno desc as
    tiebreaker) — so "oldest" here is the tail of that ordering, not
    necessarily insertion order. Deletes whatever overflow exists past
    `limit` and returns how many docs were removed. Safe to call on
    every run regardless of whether that run inserted anything new —
    it's a no-op when the collection is already at or under the cap.
    """
    total = await db.notices.count_documents({})
    if total <= limit:
        return 0
    overflow = total - limit
    cursor = (
        db.notices.find({}, {"_id": 0, "slno": 1})
        .sort([("date", 1), ("slno", 1)])  # oldest-first — the inverse of 'latest'
        .limit(overflow)
    )
    stale = await cursor.to_list(length=overflow)
    stale_slnos = [d["slno"] for d in stale]
    if not stale_slnos:
        return 0
    result = await db.notices.delete_many({"slno": {"$in": stale_slnos}})
    return result.deleted_count


def normalize_entry(entry: dict, target_year: int) -> dict | None:
    """
    Convert a raw college-API entry to our persisted shape.
    Returns None if the entry doesn't pass the current-year filter or
    lacks a usable slno/date.
    """
    slno_str = str(entry.get("slno") or "").strip()
    if not slno_str.isdigit():
        return None
    slno = int(slno_str)

    date_dt = _parse_date(entry.get("date") or "")
    if not date_dt or date_dt.year != target_year:
        return None

    # details1 is HTML-in-a-JSON-string (double-escaped). Unescape once
    # so we store real HTML; the frontend sanitizes at render.
    details_html = html.unescape(entry.get("details1") or "")

    company = _extract_company(entry, details_html)
    links = _extract_links(entry, details_html)

    files = _clean(
        [entry.get(k) for k in (
            "first_file", "second_file", "third_file",
            "fourth_file", "fifth_file",
        )]
    )
    uploads = _clean(
        [entry.get(k) for k in (
            "upload1", "upload2", "upload3", "upload4", "upload5",
        )]
    )

    is_message_only = not links and not company and not files and not uploads

    # Extract closing date once, at ingest time — persisted with the doc
    # so the card UI can display "Registration closes on ..." without
    # re-parsing on every render.
    closing_date = extract_closing_date(
        {"headline1": entry.get("headline1"), "detailsHtml": details_html}
    ) if not is_message_only else None

    # Phase 6.5 · messageExcerpt now populated for EVERY notice (not just
    # message-only ones), so card grids can render a preview without
    # re-parsing HTML on the client.
    message_excerpt = _plain_text(details_html, maxlen=200)

    # Phase 6 · badge fields — extracted once at ingest, persisted with
    # the doc so cards can render without re-parsing HTML at render time.
    # NOTE: we intentionally search the FULL plain-text body (not the
    # 200-char excerpt) so CTC / branches mentioned deep in the notice
    # are still caught.
    headline_text = (entry.get("headline1") or "").strip()
    plain_details = _plain_text(details_html)
    combined = f"{headline_text}  {plain_details}"
    ctc = None if is_message_only else extract_ctc(combined)
    branches = [] if is_message_only else extract_branches(combined)
    notice_type = (
        "notice" if is_message_only
        else extract_notice_type(headline_text, plain_details)
    )

    return {
        "slno": slno,
        "headline1": headline_text,
        "date": date_dt.isoformat(),
        "detailsHtml": details_html,
        "company": company,
        "registrationLinks": links,
        "isMessageOnly": is_message_only,
        "closingDate": closing_date,
        "messageExcerpt": message_excerpt,
        "isoWeek": iso_week_of(date_dt),
        "ctc": ctc,
        "branches": branches,
        "noticeType": notice_type,
    }


# --------------------------------------------------------------------- #
#  Network
# --------------------------------------------------------------------- #

# Retry only on failure modes that are plausibly transient (network
# blips, brief upstream downtime, momentary 5xx). A malformed response
# or a 4xx isn't going to fix itself on retry, so those still raise
# immediately on first attempt.
_RETRYABLE_EXCEPTIONS = (httpx.TimeoutException, httpx.NetworkError)


async def fetch_college_api(
    url: str,
    timeout: float = 60.0,
    *,
    max_attempts: int = 3,
    backoff_base: float = 2.0,
) -> list[dict]:
    """
    Fetch the college API with a brief exponential-backoff retry
    (2s, 4s between attempts) for transient network failures — a
    single blip on canaraengineering.in's side no longer fails the
    whole scheduled ingest run outright.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                payload = resp.json()
            if not payload.get("success"):
                raise RuntimeError(f"College API returned success=false: {payload!r}")
            return payload.get("data") or []
        except _RETRYABLE_EXCEPTIONS as exc:
            last_exc = exc
            if attempt < max_attempts:
                wait_s = backoff_base ** attempt
                logger.warning(
                    "College API fetch attempt %d/%d failed (%s: %s) — retrying in %.0fs",
                    attempt, max_attempts, type(exc).__name__, exc, wait_s,
                )
                await asyncio.sleep(wait_s)
            else:
                logger.error(
                    "College API fetch failed after %d attempts: %s: %s",
                    max_attempts, type(exc).__name__, exc,
                )
        # HTTPStatusError (4xx/5xx) and RuntimeError (success=false) are
        # NOT in _RETRYABLE_EXCEPTIONS — they propagate immediately on
        # first occurrence rather than retrying, since a bad response
        # shape or a 4xx won't resolve itself by trying again.
    raise last_exc


async def _prune_deleted_from_source(
    db: AsyncIOMotorDatabase,
    source_valid_slnos: set,
    fetched_count: int,
    *,
    min_fetched_for_safety: int = 1000,
) -> dict:
    """
    Remove notices that no longer appear in the college's own feed at
    all — e.g. the college deletes a notice and reposts a corrected
    version under a new slno (this is exactly what happened with the
    "BLACKFROG on Campus Drive" duplicate: #10943 was deleted upstream
    and replaced by #10945, but the app kept #10943 forever since
    nothing had ever told it to remove anything).

    Deliberately conservative, since a false positive here means
    silently deleting a notice a student might still need:

      1. A slno must be absent across TWO CONSECUTIVE ingest runs
         before it's actually deleted — tracked via a `missingSince`
         timestamp on the notice doc. A single missed/glitchy poll
         response can't delete anything on its own; if the slno
         reappears on the very next poll, its "missing" mark is
         cleared and nothing is removed.
      2. Skipped ENTIRELY (a safe no-op) if this run's raw fetch count
         looks anomalously small to trust as a complete listing — a
         badly truncated or partial response from the college API must
         never be treated as "everything else was deleted."
    """
    if fetched_count < min_fetched_for_safety:
        logger.warning(
            "Skipping stale-notice pruning this run: fetched=%d is "
            "below the safety floor (%d) for trusting this as a "
            "complete source listing.",
            fetched_count, min_fetched_for_safety,
        )
        return {"pruned": 0, "marked_missing": 0, "recovered": 0}

    now_iso = datetime.now(timezone.utc).isoformat()
    current_docs = await db.notices.find(
        {}, {"_id": 0, "slno": 1, "missingSince": 1}
    ).to_list(length=None)

    to_delete: list[int] = []
    to_mark_missing: list[int] = []
    to_clear: list[int] = []

    for d in current_docs:
        slno = d["slno"]
        still_present = slno in source_valid_slnos
        was_missing = bool(d.get("missingSince"))
        if still_present:
            if was_missing:
                to_clear.append(slno)
        elif was_missing:
            to_delete.append(slno)
        else:
            to_mark_missing.append(slno)

    if to_mark_missing:
        await db.notices.update_many(
            {"slno": {"$in": to_mark_missing}},
            {"$set": {"missingSince": now_iso}},
        )
    if to_clear:
        await db.notices.update_many(
            {"slno": {"$in": to_clear}},
            {"$set": {"missingSince": None}},
        )
    if to_delete:
        await db.notices.delete_many({"slno": {"$in": to_delete}})
        logger.info(
            "Pruned %d notice(s) confirmed removed from the source "
            "feed across two consecutive polls: %s",
            len(to_delete), to_delete,
        )

    return {
        "pruned": len(to_delete),
        "marked_missing": len(to_mark_missing),
        "recovered": len(to_clear),
    }


# --------------------------------------------------------------------- #
#  Main entry point
# --------------------------------------------------------------------- #
async def run_ingest(
    db: AsyncIOMotorDatabase,
    college_api_url: str,
    *,
    target_year: int | None = None,
    notify: bool = True,
    notify_topic: str = "placement-updates",
) -> dict:
    """
    One poll cycle. Idempotent: safe to run concurrently / repeatedly.
    Returns the ingest_run audit document (already persisted).

    When `notify=True` (default), a push is fanned out to `notify_topic`
    for every genuinely-new slno inserted this run, and `notifiedAt` is
    stamped on success. Pass `notify=False` for backfill / dry-run.
    """
    started_at = datetime.now(timezone.utc)
    ist_now = datetime.now(IST)
    year = target_year if target_year is not None else ist_now.year

    run = {
        "started_at": started_at.isoformat(),
        "ist_hour": ist_now.hour,
        "target_year": year,
        "fetched": 0,
        "inserted": 0,
        "updated": 0,
        "skipped_wrong_year_or_invalid": 0,
        "new_slnos": [],
        "notify": notify,
        "published": 0,
        "publish_failed": 0,
        "publish_batched": False,
        "publish_message_ids": [],
        "publish_errors": [],
        "capped_deleted": 0,
        "pruned_deleted_from_source": 0,
        "marked_missing_from_source": 0,
        "error": None,
        "finished_at": None,
    }

    try:
        # Ensure indexes (idempotent)
        await db.notices.create_index("slno", unique=True)
        await db.notices.create_index([("date", -1), ("slno", -1)])
        await db.notices.create_index("isoWeek")
        # `seen_slnos` has no index to add — Mongo's default _id index
        # (we store slno itself as _id) already enforces uniqueness.

        entries = await fetch_college_api(college_api_url)
        run["fetched"] = len(entries)
        logger.info("Ingest: fetched %d entries from college API", len(entries))

        inserted = updated = skipped = 0
        new_slnos: list[int] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        # `notices` is capped (Phase 8, below) — old entries get deleted
        # once the collection exceeds NOTICE_CAP, even though the
        # college API keeps re-listing them on every poll. If "is this
        # new?" were judged purely by "not currently in `notices`",
        # every capped-out-then-recycled-back entry would look new
        # again on a later poll and re-trigger a push — which is
        # exactly what was happening (a notification on effectively
        # every scheduled tick, and near-permanent overflow past
        # MAX_INDIVIDUAL_PUSHES_PER_RUN forcing the generic batch
        # message instead of real company names).
        #
        # `seen_slnos` is a separate, NEVER-trimmed record of every
        # slno ever encountered — used only to decide "is this
        # genuinely new for notification purposes", independent of
        # whatever `notices` currently holds for display/storage.
        existing_notice_ids = {
            d["slno"] for d in await db.notices.find({}, {"_id": 0, "slno": 1}).to_list(length=None)
        }
        ever_seen_ids = {
            d["_id"] for d in await db.seen_slnos.find({}, {"_id": 1}).to_list(length=None)
        }

        newly_seen_this_run: set[int] = set()
        new_docs_for_notify: list[dict] = []
        source_valid_slnos_this_run: set[int] = set()

        for raw in entries:
            doc = normalize_entry(raw, year)
            if doc is None:
                skipped += 1
                continue

            slno = doc["slno"]
            source_valid_slnos_this_run.add(slno)
            if slno in existing_notice_ids:
                await db.notices.update_one(
                    {"slno": slno},
                    {"$set": {**doc, "updatedAt": now_iso}},
                )
                updated += 1
            else:
                await db.notices.insert_one(
                    {
                        **doc,
                        "firstSeenAt": now_iso,
                        "updatedAt": now_iso,
                        "notifiedAt": None,
                    }
                )
                inserted += 1
                new_slnos.append(slno)

            if slno not in ever_seen_ids and slno not in newly_seen_this_run:
                newly_seen_this_run.add(slno)
                new_docs_for_notify.append(doc)

        if newly_seen_this_run:
            try:
                await db.seen_slnos.insert_many(
                    [{"_id": s} for s in newly_seen_this_run], ordered=False
                )
            except Exception:  # noqa: BLE001 — e.g. duplicate-key on a
                # concurrent run; harmless, the point (recorded as
                # seen) is already achieved by whichever run won.
                logger.warning(
                    "seen_slnos insert_many had partial failures (likely "
                    "a concurrent ingest run) — non-fatal.",
                )

        run.update(
            {
                "inserted": inserted,
                "updated": updated,
                "skipped_wrong_year_or_invalid": skipped,
                "new_slnos": new_slnos,
            }
        )
        logger.info(
            "Ingest done: inserted=%d updated=%d skipped=%d new_in_notices=%d "
            "genuinely_new_for_notify=%d",
            inserted, updated, skipped, len(new_slnos), len(new_docs_for_notify),
        )

        # Fan out FCM pushes for GENUINELY new entries only (see
        # new_docs_for_notify comment above) — not merely
        # newly-(re)inserted-into-the-capped-collection entries.
        if notify and new_docs_for_notify:
            pub = await _publish_new_notices(new_docs_for_notify, notify_topic)
            run.update(
                {
                    "published": pub["published"],
                    "publish_failed": pub["failed"],
                    "publish_batched": pub["batched"],
                    "publish_message_ids": pub["message_ids"],
                    "publish_errors": pub["errors"],
                }
            )
            # Stamp notifiedAt on exactly the slnos that were
            # confirmed published — not "the first N of the input
            # list", since individual publishes can fail independently
            # of each other and successes aren't guaranteed to be a
            # contiguous prefix.
            if pub["published_slnos"]:
                await db.notices.update_many(
                    {"slno": {"$in": pub["published_slnos"]}},
                    {"$set": {"notifiedAt": now_iso}},
                )

        # Remove notices genuinely deleted upstream (confirmed absent
        # across two consecutive polls — see _prune_deleted_from_source
        # for why this needs to be conservative). Runs BEFORE the cap
        # trim below: these are two distinct concerns — this one is
        # "the source no longer has this notice at all", the cap trim
        # is "keep storage bounded regardless of source state" — and
        # source-deletion pruning should take priority when both would
        # otherwise remove the same notice.
        prune = await _prune_deleted_from_source(
            db, source_valid_slnos_this_run, run["fetched"],
        )
        run["pruned_deleted_from_source"] = prune["pruned"]
        run["marked_missing_from_source"] = prune["marked_missing"]

        # Phase 8 · trim back down to NOTICE_CAP every run, regardless of
        # whether this run inserted anything new — keeps storage bounded
        # without needing a separate cleanup job.
        deleted = await _enforce_notice_cap(db, NOTICE_CAP)
        run["capped_deleted"] = deleted
        if deleted:
            logger.info("Ingest: trimmed %d oldest notice(s) to stay at cap=%d", deleted, NOTICE_CAP)
    except Exception as exc:  # noqa: BLE001 — record failure, don't crash scheduler
        logger.exception("Ingest run failed")
        run["error"] = f"{type(exc).__name__}: {exc}"
        notify_discord(
            "Placement Pulse — ingest run failed",
            f"```{run['error']}```",
            severity="error",
            fields={"target_year": year, "fetched": run.get("fetched", 0)},
        )
    finally:
        run["finished_at"] = datetime.now(timezone.utc).isoformat()
        try:
            await db.ingest_runs.insert_one({**run})
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to persist ingest_run audit doc")
            # If we can't even write the audit doc, that's usually Mongo
            # itself being unreachable — the strongest available signal
            # of a database outage, worth a distinct alert.
            notify_discord(
                "Placement Pulse — MongoDB unreachable",
                f"Ingest run completed but the audit doc write failed:\n```{type(exc).__name__}: {exc}```",
                severity="error",
            )

    # Strip any _id Motor injected on our dict in-place
    run.pop("_id", None)
    return run