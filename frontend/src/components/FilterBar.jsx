import { useEffect, useMemo, useRef, useState } from "react";

/**
 * FilterBar — server-side + client-side hybrid.
 *   • q, company, hasRegistration, noticeType  → GET /api/entries params
 *   • branches (chips, now behind a dropdown)   → applied client-side
 *   • week (now a dropdown, was pills)           → server-side (isoWeek)
 */
const NOTICE_TYPES = [
  { value: "any",        label: "All types" },
  { value: "drive",      label: "Placement drive" },
  { value: "internship", label: "Internship" },
  { value: "results",    label: "Results" },
  { value: "ppt",        label: "Pre-placement talk" },
  { value: "notice",     label: "Announcement" },
];

const HAS_REG_OPTS = [
  { value: "any", label: "All notices" },
  { value: "yes", label: "Has registration link" },
  { value: "no",  label: "No registration link" },
];

const BRANCH_ORDER = ["CSE", "ISE", "IT", "ECE", "EEE", "ME", "CV", "AIML", "DS", "MBA", "MCA"];

export const DEFAULT_FILTERS = {
  q: "",
  company: "any",
  branches: [],
  noticeType: "any",
  hasRegistration: "any",   // "any" | "yes" | "no"
  week: "any",              // "any" | ISO week label
};

// --- helpers exported for Home.jsx ---------------------------------

export function hasAnyServerFilter(f) {
  if (!f) return false;
  return (
    (f.q && f.q.trim().length > 0) ||
    (f.company && f.company !== "any") ||
    (f.noticeType && f.noticeType !== "any") ||
    (f.hasRegistration && f.hasRegistration !== "any") ||
    (f.week && f.week !== "any")
  );
}

export function buildEntriesQuery(f, limit = 200) {
  const p = new URLSearchParams();
  p.set("limit", String(limit));
  if (f.q && f.q.trim()) p.set("q", f.q.trim());
  if (f.company && f.company !== "any") p.set("company", f.company);
  if (f.noticeType && f.noticeType !== "any") p.set("noticeType", f.noticeType);
  if (f.hasRegistration === "yes") p.set("hasRegistration", "true");
  else if (f.hasRegistration === "no") p.set("hasRegistration", "false");
  if (f.week && f.week !== "any") p.set("week", f.week);
  return p.toString();
}

export function applyClientBranchFilter(entries, branches) {
  if (!entries) return entries;
  if (!branches || branches.length === 0) return entries;
  return entries.filter((e) => {
    const eb = e.branches || [];
    if (eb.includes("ALL")) return true;
    return branches.every((b) => eb.includes(b));
  });
}

// -------------------------------------------------------------------

export default function FilterBar({ entries, value, onChange, currentWeek }) {
  const filters = value || DEFAULT_FILTERS;
  const [branchOpen, setBranchOpen] = useState(false);
  const branchRef = useRef(null);
  const branchToggleRef = useRef(null);

  const companies = useMemo(() => {
    const set = new Set();
    (entries || []).forEach((e) => {
      const c = (e.company || "").trim();
      if (c) set.add(c);
    });
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [entries]);

  const weeks = useMemo(() => {
    const set = new Set();
    (entries || []).forEach((e) => {
      if (e.isoWeek) set.add(e.isoWeek);
    });
    return Array.from(set).sort().reverse();
  }, [entries]);

  const setField = (k, v) => onChange({ ...filters, [k]: v });

  const toggleBranch = (code) => {
    const cur = new Set(filters.branches || []);
    if (cur.has(code)) cur.delete(code);
    else cur.add(code);
    setField("branches", Array.from(cur));
  };

  // Close the branch dropdown on outside click or Escape — either way,
  // return focus to the toggle button so keyboard users aren't dropped
  // back at the top of the page.
  useEffect(() => {
    if (!branchOpen) return undefined;
    const closeAndRefocus = () => {
      setBranchOpen(false);
      branchToggleRef.current?.focus();
    };
    const onDocClick = (e) => {
      if (branchRef.current && !branchRef.current.contains(e.target)) {
        setBranchOpen(false); // outside click: no refocus, user clicked elsewhere on purpose
      }
    };
    const onKeyDown = (e) => {
      if (e.key === "Escape") closeAndRefocus();
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [branchOpen]);

  const branchSummary =
    (filters.branches || []).length > 0
      ? `${filters.branches.length} branch${filters.branches.length > 1 ? "es" : ""}`
      : "All branches";

  const activeCount =
    (filters.q ? 1 : 0) +
    (filters.company !== "any" ? 1 : 0) +
    (filters.branches?.length ? 1 : 0) +
    (filters.noticeType !== "any" ? 1 : 0) +
    (filters.hasRegistration !== "any" ? 1 : 0) +
    (filters.week !== "any" ? 1 : 0);

  return (
    <section
      className="pp-filterbar"
      data-testid="filter-bar"
      aria-label="Search and filter notices"
    >
      <div className="pp-filter-row">
        <label htmlFor="pp-filter-q" className="pp-sr-only">
          Search notices
        </label>
        <input
          id="pp-filter-q"
          type="search"
          className="pp-filter-input"
          placeholder="Search company, keyword, headline…"
          value={filters.q}
          onChange={(e) => setField("q", e.target.value)}
          data-testid="filter-search"
          autoComplete="off"
        />
        <select
          className="pp-filter-select"
          value={filters.company}
          onChange={(e) => setField("company", e.target.value)}
          aria-label="Company"
          data-testid="filter-company"
        >
          <option value="any">All companies</option>
          {companies.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <select
          className="pp-filter-select"
          value={filters.noticeType}
          onChange={(e) => setField("noticeType", e.target.value)}
          aria-label="Notice type"
          data-testid="filter-type"
        >
          {NOTICE_TYPES.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
        <select
          className="pp-filter-select"
          value={filters.hasRegistration}
          onChange={(e) => setField("hasRegistration", e.target.value)}
          aria-label="Registration availability"
          data-testid="filter-has-registration"
        >
          {HAS_REG_OPTS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
        <select
          className="pp-filter-select"
          value={filters.week}
          onChange={(e) => setField("week", e.target.value)}
          aria-label="Week"
          data-testid="filter-week-select"
        >
          <option value="any">All weeks</option>
          {currentWeek && (
            <option value={currentWeek}>This week · {currentWeek}</option>
          )}
          {weeks.slice(0, 8).filter((w) => w !== currentWeek).map((w) => (
            <option key={w} value={w}>{w}</option>
          ))}
        </select>

        <div className="pp-branch-dropdown" ref={branchRef}>
          <button
            type="button"
            ref={branchToggleRef}
            className="pp-filter-select pp-branch-dropdown-btn"
            onClick={() => setBranchOpen((o) => !o)}
            aria-expanded={branchOpen}
            aria-haspopup="true"
            data-testid="filter-branch-toggle"
          >
            {branchSummary}
          </button>
          {branchOpen && (
            <div
              className="pp-branch-dropdown-panel"
              role="group"
              aria-label="Branches"
              data-testid="filter-branch-panel"
            >
              {BRANCH_ORDER.map((code) => {
                const active = (filters.branches || []).includes(code);
                return (
                  <label key={code} className="pp-branch-option">
                    <input
                      type="checkbox"
                      checked={active}
                      onChange={() => toggleBranch(code)}
                      data-testid={`filter-branch-${code}`}
                    />
                    {code}
                  </label>
                );
              })}
              {(filters.branches || []).length > 0 && (
                <button
                  type="button"
                  className="pp-branch-clear"
                  onClick={() => setField("branches", [])}
                  data-testid="filter-branch-clear"
                >
                  Clear branches
                </button>
              )}
            </div>
          )}
        </div>
      </div>

      {activeCount > 0 && (
        <div className="pp-filter-chips">
          <button
            type="button"
            className="pp-filter-reset"
            onClick={() => onChange(DEFAULT_FILTERS)}
            data-testid="filter-reset"
          >
            Clear all filters ({activeCount})
          </button>
        </div>
      )}
    </section>
  );
}