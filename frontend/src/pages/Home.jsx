import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import { Link } from "react-router-dom";
import {
  subscribeToPlacementUpdates,
  listenForForegroundMessages,
  currentPermission,
} from "@/lib/firebase";
import { clearBadge, hasBadgingApi, isStandalone } from "@/lib/pwa";
import {
  cacheEntries,
  loadCachedEntries,
  getLastSyncedAt,
  getLastSeenSlno,
  setLastSeenSlno,
  addBookmark,
  removeBookmark,
  getAllBookmarkSlnos,
} from "@/lib/db";
import InstallPrompt from "@/components/InstallPrompt";
import EntryCard from "@/components/EntryCard";
import NotificationBell from "@/components/NotificationBell";
import MonthSection from "@/components/MonthSection";
import SkeletonCard from "@/components/SkeletonCard";
import ScrollToTopButton from "@/components/ScrollToTopButton";
import SortControl, { applySort } from "@/components/SortControl";
import FilterBar, {
  DEFAULT_FILTERS,
  applyClientBranchFilter,
  buildEntriesQuery,
  hasAnyServerFilter,
} from "@/components/FilterBar";
import StatsStrip from "@/components/StatsStrip";
import CurrentWeekChip, {
  getCurrentWeekViewedAt,
  markCurrentWeekViewed,
} from "@/components/CurrentWeekChip";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// Load ALL entries in pages (used only when no server filter active).
async function fetchAllEntries() {
  const first = await axios.get(`${API}/entries?limit=50`);
  const entries = first.data.entries || [];
  let cursor = first.data.next_cursor;
  for (let page = 0; page < 5 && cursor; page += 1) {
    try {
      const r = await axios.get(`${API}/entries?limit=100&cursor=${cursor}`);
      const batch = r.data.entries || [];
      entries.push(...batch);
      cursor = r.data.next_cursor;
      if (batch.length === 0) break;
    } catch (_) {
      break;
    }
  }
  return entries;
}

async function fetchFilteredEntries(filters) {
  const qs = buildEntriesQuery(filters, 200);
  const r = await axios.get(`${API}/entries?${qs}`);
  return r.data.entries || [];
}

function weekOfMonth(day) {
  return Math.ceil(day / 7); // 1..5
}

function monthKey(dateStr) {
  const d = new Date(dateStr);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function monthLabel(dateStr) {
  const d = new Date(dateStr);
  return d.toLocaleDateString("en-IN", { month: "long", year: "numeric" });
}

// Top-level grouping is now Month -> Week-of-month, instead of a flat
// list of ISO weeks. A month only ever appears here if it has at least
// one entry — there's no fixed Jan-Dec scaffold, so a month with zero
// drives simply never renders, and starts appearing automatically the
// moment its first notice lands.
function groupByMonthThenWeek(entries) {
  const months = new Map();
  for (const e of entries) {
    if (!e.date) continue;
    const d = new Date(e.date);
    const mKey = monthKey(e.date);
    const mLabel = monthLabel(e.date);
    const wNum = weekOfMonth(d.getDate());
    if (!months.has(mKey)) months.set(mKey, { label: mLabel, weeks: new Map() });
    const month = months.get(mKey);
    if (!month.weeks.has(wNum)) month.weeks.set(wNum, []);
    month.weeks.get(wNum).push(e);
  }
  // Months newest-first; weeks within each month newest-first too, so
  // MonthSection's default page (index 0) is always the latest week.
  return Array.from(months.entries())
    .sort((a, b) => (a[0] < b[0] ? 1 : -1))
    .map(([mKey, m]) => {
      const weekArr = Array.from(m.weeks.entries())
        .sort((a, b) => b[0] - a[0])
        .map(([wNum, list]) => [wNum, list]);
      return [mKey, m.label, weekArr];
    });
}

function formatSyncedAt(iso) {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  const now = Date.now();
  const diffMin = Math.round((now - then) / 60000);
  if (diffMin < 2) return "just now";
  if (diffMin < 60) return `${diffMin} min ago`;
  const h = Math.round(diffMin / 60);
  return h < 24 ? `${h} h ago` : `${Math.round(h / 24)} d ago`;
}

export default function Home() {
  // Two-tier state: `allEntries` = the un-filtered snapshot (used when no
  // server-side filter is active). `entries` = current visible set.
  const [allEntries, setAllEntries] = useState(null);
  const [entries, setEntries] = useState(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [syncedAt, setSyncedAt] = useState(null);
  const [offline, setOffline] = useState(false);

  const [sortMode, setSortMode] = useState("newest");
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [lastSeenSlno, setLastSeenSlnoState] = useState(0);
  const [bookmarkedSlnos, setBookmarkedSlnos] = useState(new Set());
  const [showBookmarksOnly, setShowBookmarksOnly] = useState(false);

  const [notifState, setNotifState] = useState({
    permission: currentPermission(),
    subscribing: false,
    token: null,
  });

  // ---- bookmarks: load once on mount ---------------------------------
  useEffect(() => {
    let cancelled = false;
    getAllBookmarkSlnos().then((set) => {
      if (!cancelled) setBookmarkedSlnos(set);
    }).catch(() => {
      /* IndexedDB unavailable (e.g. private browsing) — bookmarking
         just won't persist; not worth surfacing an error for this. */
    });
    return () => { cancelled = true; };
  }, []);

  const toggleBookmark = useCallback((entry) => {
    const slno = entry?.slno;
    if (!slno) return;
    setBookmarkedSlnos((prev) => {
      const next = new Set(prev);
      if (next.has(slno)) {
        next.delete(slno);
        removeBookmark(slno).catch(() => {});
      } else {
        next.add(slno);
        addBookmark(slno).catch(() => {});
      }
      return next;
    });
  }, []);

  // ---- initial load (all entries, cache-first then network) --------
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const cached = await loadCachedEntries();
      if (cached.length > 0 && !cancelled) {
        setAllEntries(cached);
        setEntries(cached);
        setLoading(false);
        const at = await getLastSyncedAt();
        setSyncedAt(at);
      }
      try {
        const fresh = await fetchAllEntries();
        if (!cancelled) {
          setAllEntries(fresh);
          if (!hasAnyServerFilter(filters)) setEntries(fresh);
          setLoading(false);
          setOffline(false);
        }
        await cacheEntries(fresh);
        const at = await getLastSyncedAt();
        if (!cancelled) setSyncedAt(at);
      } catch (e) {
        if (cached.length > 0) {
          if (!cancelled) setOffline(true);
        } else if (!cancelled) {
          setError(e?.message || "Failed to load notices.");
          setLoading(false);
        }
      }
      const lss = await getLastSeenSlno();
      if (!cancelled) setLastSeenSlnoState(lss || 0);
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- foreground push handler ---------
  useEffect(() => {
    const unsub = listenForForegroundMessages((_msg) => {
      // Re-fetch quietly; user will see the update within the current view.
      fetchAllEntries().then((fresh) => {
        setAllEntries(fresh);
        if (!hasAnyServerFilter(filters)) setEntries(fresh);
        cacheEntries(fresh);
      }).catch(() => {});
    });
    return () => unsub && unsub();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- server-side filter fetch -------------------------------------
  // Only the free-text search box benefits from debouncing (so we're
  // not firing a request per keystroke). Dropdown selections (company,
  // type, registration, week) are single discrete actions — those
  // should hit the API immediately, not wait out an artificial delay.
  const [debouncedQ, setDebouncedQ] = useState(filters.q);
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(filters.q), 300);
    return () => clearTimeout(t);
  }, [filters.q]);

  useEffect(() => {
    if (allEntries === null) return; // still loading initial
    const effectiveFilters = { ...filters, q: debouncedQ };
    if (!hasAnyServerFilter(effectiveFilters)) {
      // Reset to the full snapshot without network round-trip.
      setEntries(allEntries);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const list = await fetchFilteredEntries(effectiveFilters);
        if (!cancelled) setEntries(list);
      } catch (_) {
        // Fallback: server-filter failure → filter client-side.
        if (!cancelled) setEntries(allEntries);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQ, filters.company, filters.hasRegistration, filters.noticeType, filters.week, allEntries]);

  // Apply the client-side branch chips on top of whatever `entries` holds.
  const filtered = useMemo(
    () => (entries ? applyClientBranchFilter(entries, filters.branches) : null),
    [entries, filters.branches],
  );

  const groupedByMonth = useMemo(() => {
    if (!filtered) return [];
    return groupByMonthThenWeek(filtered).map(([mKey, mLabel, weeks]) => [
      mKey,
      mLabel,
      weeks.map(([wNum, list]) => [wNum, applySort(list, sortMode)]),
    ]);
  }, [filtered, sortMode]);

  // Sorting inside each month/week page (above) only reorders the
  // handful of cards on whatever page happens to be showing — picking
  // "Company A-Z" or "Oldest posted" would barely look like it did
  // anything, since the month/week structure itself stays date-driven
  // regardless of sortMode. For any sort mode other than the default
  // "newest" (which already matches the grouping's own order), show
  // one flat, fully-sorted grid instead of the paged month/week view.
  const useFlatSortedView = sortMode !== "newest";
  const flatSorted = useMemo(() => {
    if (!filtered) return [];
    return applySort(filtered, sortMode);
  }, [filtered, sortMode]);

  // Bookmarks view is deliberately independent of the active
  // company/branch/week filters — the point of "my bookmarks" is to
  // always show everything saved, not whatever subset happens to
  // match today's filter selection.
  const bookmarkedEntries = useMemo(() => {
    if (!allEntries || bookmarkedSlnos.size === 0) return [];
    const matched = allEntries.filter((e) => bookmarkedSlnos.has(e.slno));
    return applySort(matched, sortMode);
  }, [allEntries, bookmarkedSlnos, sortMode]);

  // Current ISO week from the newest known entry.
  const currentWeek = useMemo(() => {
    const src = allEntries || entries;
    if (!src || src.length === 0) return null;
    let best = null;
    for (const e of src) {
      if (!best || (e.date || "") > (best.date || "")) best = e;
    }
    return best?.isoWeek || null;
  }, [allEntries, entries]);

  // Same idea, one level up: current month from the newest known entry
  // rather than wall-clock time, so it stays consistent with cached
  // data when offline.
  const currentMonthKey = useMemo(() => {
    const src = allEntries || entries;
    if (!src || src.length === 0) return null;
    let best = null;
    for (const e of src) {
      if (!best || (e.date || "") > (best.date || "")) best = e;
    }
    return best?.date ? monthKey(best.date) : null;
  }, [allEntries, entries]);

  const currentWeekCount = useMemo(() => {
    if (!currentWeek || !filtered) return 0;
    return filtered.filter((e) => e.isoWeek === currentWeek).length;
  }, [currentWeek, filtered]);

  // Unread-within-current-week = entries in the current week whose
  // firstSeenAt is after the timestamp the user last opened that week.
  const [weekViewedAt, setWeekViewedAt] = useState(() => getCurrentWeekViewedAt());
  const currentWeekUnread = useMemo(() => {
    if (!currentWeek || !filtered) return 0;
    if (!weekViewedAt) return currentWeekCount; // never viewed → all unread
    return filtered.filter(
      (e) => e.isoWeek === currentWeek &&
             e.firstSeenAt && new Date(e.firstSeenAt) > new Date(weekViewedAt),
    ).length;
  }, [currentWeek, filtered, currentWeekCount, weekViewedAt]);

  // ---- mark badge cleared, latest-seen bookkeeping -----------------
  useEffect(() => {
    if (!allEntries || allEntries.length === 0) return;
    const topSlno = Math.max(...allEntries.map((e) => e.slno));
    if (topSlno > lastSeenSlno) {
      setLastSeenSlno(topSlno).catch(() => {});
      setLastSeenSlnoState(topSlno);
    }
    if (isStandalone() && hasBadgingApi()) clearBadge();
  }, [allEntries, lastSeenSlno]);

  const jumpToCurrentWeek = () => {
    markCurrentWeekViewed();
    setWeekViewedAt(new Date().toISOString());
  };

  return (
    <>
      {/*
        pp-shell = full-bleed responsive padding + background (defined in
        App.css). pp-container = centered, max-width content column. Both
        classes already existed in App.css but were never applied to any
        real markup here — that's why the page rendered flush-left with
        no responsive padding regardless of viewport width. Restoring
        them (rather than the unstyled "pp-page" class) is what actually
        activates the centering fix.
      */}
      <main className="pp-shell" data-testid="home-page">
        <div className="pp-container">
          <div className="pp-topbar" data-testid="app-topbar">
            <img
              src="/logo.svg"
              alt="Placement Pulse"
              className="pp-logo"
              data-testid="app-logo"
            />
            <div className="pp-topbar-actions">
              <NotificationBell />
            </div>
          </div>

          <figure className="pp-hero" data-testid="app-hero">
            <img
              src="/hero-college.jpg"
              alt="Canara Engineering College campus"
              data-testid="hero-img"
              loading="eager"
              decoding="async"
            />
            <figcaption className="pp-hero-caption">
              Canara Engineering College · Bantwal
            </figcaption>
          </figure>

          <StatsStrip />

          <FilterBar
            entries={allEntries || []}
            value={filters}
            onChange={setFilters}
            currentWeek={currentWeek}
          />

          <header className="pp-header">
            <div>
              <h1 className="pp-title">Placement Pulse</h1>
              <CurrentWeekChip
                currentWeek={currentWeek}
                currentMonthKey={currentMonthKey}
                weekCount={currentWeekCount}
                unreadCount={currentWeekUnread}
                onView={jumpToCurrentWeek}
              />
            </div>

            <div className="pp-header-actions">
              <button
                type="button"
                className={`pp-bookmarks-toggle${showBookmarksOnly ? " is-active" : ""}`}
                onClick={() => setShowBookmarksOnly((v) => !v)}
                aria-pressed={showBookmarksOnly}
                data-testid="bookmarks-toggle"
                title={showBookmarksOnly ? "Show all notices" : "Show only my bookmarks"}
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill={showBookmarksOnly ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />
                </svg>
                Bookmarks{bookmarkedSlnos.size > 0 ? ` (${bookmarkedSlnos.size})` : ""}
              </button>
              <SortControl value={sortMode} onChange={setSortMode} />
            </div>
          </header>

          <InstallPrompt
            notifState={notifState}
            setNotifState={setNotifState}
            subscribe={subscribeToPlacementUpdates}
          />

          <section className="pp-feed" data-testid="feed">
            {loading && !entries && (
              <div className="pp-week-body" data-testid="loading-skeleton" aria-label="Loading notices">
                {Array.from({ length: 6 }).map((_, i) => (
                  <SkeletonCard key={i} />
                ))}
              </div>
            )}
            {error && (
              <p className="pp-empty pp-error" data-testid="error">{error}</p>
            )}

            {entries && entries.length === 0 && !hasAnyServerFilter(filters) &&
              (filters.branches || []).length === 0 && (
                <p className="pp-empty" data-testid="entries-empty">
                  No notices yet for the current year.
                </p>
            )}

            {filtered && filtered.length === 0 &&
              (hasAnyServerFilter(filters) || (filters.branches || []).length > 0) && (
                <p className="pp-empty" data-testid="entries-empty-filtered">
                  No notices match the current filters.{" "}
                  <button
                    type="button"
                    className="pp-btn-link"
                    onClick={() => setFilters(DEFAULT_FILTERS)}
                    data-testid="entries-empty-reset"
                  >
                    Clear all filters
                  </button>
                </p>
            )}

            {showBookmarksOnly && bookmarkedEntries.length === 0 && (
              <p className="pp-empty" data-testid="bookmarks-empty">
                No bookmarks yet. Tap the bookmark icon on any card to
                save it here.
              </p>
            )}

            {showBookmarksOnly ? (
              <div className="pp-week-body" data-testid="bookmarks-feed">
                {bookmarkedEntries.map((e) => (
                  <EntryCard
                    key={e.slno}
                    entry={e}
                    isUnread={false}
                    isBookmarked
                    onToggleBookmark={toggleBookmark}
                  />
                ))}
              </div>
            ) : useFlatSortedView ? (
              <div className="pp-week-body" data-testid="flat-sorted-feed">
                {flatSorted.map((e) => (
                  <EntryCard
                    key={e.slno}
                    entry={e}
                    isUnread={e.slno > lastSeenSlno}
                    isBookmarked={bookmarkedSlnos.has(e.slno)}
                    onToggleBookmark={toggleBookmark}
                  />
                ))}
              </div>
            ) : (
              groupedByMonth.map(([mKey, mLabel, weeks]) => (
                <MonthSection
                  key={mKey}
                  monthKey={mKey}
                  monthLabel={mLabel}
                  weeks={weeks}
                  lastSeenSlno={lastSeenSlno}
                  currentWeek={currentWeek}
                  weekViewedAt={weekViewedAt}
                  isCurrentMonth={mKey === currentMonthKey}
                  bookmarkedSlnos={bookmarkedSlnos}
                  onToggleBookmark={toggleBookmark}
                />
              ))
            )}
          </section>

          <footer className="pp-footer" data-testid="footer">
            <Link to="/about" className="pp-footer-link">About</Link>
            <span aria-hidden> · </span>
            <a
              href="https://canaraengineering.in/"
              target="_blank"
              rel="noopener noreferrer"
              className="pp-footer-link"
            >
              canaraengineering.in
            </a>
            <span className="pp-footer-credit" data-testid="footer-credit">
              Built by Sumit-S23
            </span>
          </footer>
        </div>
      </main>
      <ScrollToTopButton />
    </>
  );
}