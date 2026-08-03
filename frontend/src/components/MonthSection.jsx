import { useState } from "react";
import EntryCard from "@/components/EntryCard";

/**
 * MonthSection — top-level feed grouping is now by month; within a
 * month, weeks (partitioned by day-of-month ÷ 7 — days 1-7 = Week 1,
 * 8-14 = Week 2, etc.) are shown one page at a time via the pager
 * below, instead of all stacked at once. `weeks` arrives pre-sorted
 * newest-week-first, so the default page (index 0) is always the most
 * recent week in that month.
 *
 * Unread bookkeeping is unchanged from the old WeekSection — each
 * entry still carries its own real `isoWeek` from the backend
 * regardless of how it's grouped for display here, so the same
 * per-entry "is this in the current ISO week, and was it first seen
 * after the user last viewed that week" check still works exactly as
 * before.
 */
export default function MonthSection({
  monthKey,
  monthLabel,
  weeks, // [[weekNum, entries[]], ...] sorted newest week first
  lastSeenSlno = 0,
  currentWeek = null,
  weekViewedAt = null,
  isCurrentMonth = false,
  bookmarkedSlnos = null,
  onToggleBookmark = null,
}) {
  const [open, setOpen] = useState(isCurrentMonth);
  const [pageIndex, setPageIndex] = useState(0);
  const totalWeeks = weeks.length;
  const clampedIndex = Math.min(pageIndex, Math.max(totalWeeks - 1, 0));
  const [weekNum, entries] = weeks[clampedIndex] || [null, []];

  const totalNotices = weeks.reduce((sum, [, list]) => sum + list.length, 0);
  const cutoff = weekViewedAt ? new Date(weekViewedAt).getTime() : null;

  const goNewer = () => setPageIndex((i) => Math.max(i - 1, 0));
  const goOlder = () => setPageIndex((i) => Math.min(i + 1, totalWeeks - 1));

  return (
    <section
      className={`pp-month${isCurrentMonth ? " is-current" : ""}${open ? " is-open" : ""}`}
      data-testid={`month-${monthKey}`}
    >
      <button
        type="button"
        className="pp-month-header"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        data-testid={`month-toggle-${monthKey}`}
      >
        <span className="pp-month-label">
          {monthLabel}
          {isCurrentMonth && (
            <span
              className="pp-week-badge-current"
              data-testid={`month-badge-current-${monthKey}`}
            >
              Current
            </span>
          )}
        </span>
        <span className="pp-month-count">
          {totalNotices} {totalNotices === 1 ? "notice" : "notices"}
        </span>
        <span className={`pp-week-caret${open ? " is-open" : ""}`} aria-hidden>
          ▾
        </span>
      </button>

      {open && weekNum != null && (
        <>
          <div className="pp-week-pager" data-testid={`week-pager-${monthKey}`}>
            <button
              type="button"
              className="pp-week-pager-btn"
              onClick={goNewer}
              disabled={clampedIndex === 0}
              aria-label="Newer week"
              data-testid={`week-pager-newer-${monthKey}`}
            >
              ‹
            </button>
            <span className="pp-week-pager-label">
              Week {weekNum}
              <span className="pp-mono">
                {" "}
                · {entries.length} {entries.length === 1 ? "notice" : "notices"}
              </span>
              <span className="pp-week-pager-pos">
                {" "}
                ({clampedIndex + 1} of {totalWeeks})
              </span>
            </span>
            <button
              type="button"
              className="pp-week-pager-btn"
              onClick={goOlder}
              disabled={clampedIndex === totalWeeks - 1}
              aria-label="Older week"
              data-testid={`week-pager-older-${monthKey}`}
            >
              ›
            </button>
          </div>

          <div className="pp-week-body" data-testid={`month-week-body-${monthKey}`}>
            {entries.map((e) => {
              const seenBySlno = e.slno > lastSeenSlno;
              const seenByWeek =
                e.isoWeek === currentWeek &&
                cutoff &&
                e.firstSeenAt &&
                new Date(e.firstSeenAt).getTime() > cutoff;
              return (
                <EntryCard
                  key={e.slno}
                  entry={e}
                  isUnread={seenBySlno || seenByWeek}
                  isBookmarked={bookmarkedSlnos ? bookmarkedSlnos.has(e.slno) : false}
                  onToggleBookmark={onToggleBookmark}
                />
              );
            })}
          </div>
        </>
      )}
    </section>
  );
}