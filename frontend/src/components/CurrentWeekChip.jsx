const LS_KEY = "pp.currentWeekViewedAt";

export function getCurrentWeekViewedAt() {
  try {
    return localStorage.getItem(LS_KEY);
  } catch {
    return null;
  }
}

export function markCurrentWeekViewed() {
  try {
    const iso = new Date().toISOString();
    localStorage.setItem(LS_KEY, iso);
    return iso;
  } catch {
    return null;
  }
}

/**
 * Inline chip shown next to the "Placement Pulse" title (not a fixed
 * floating pill anymore — it lives in normal document flow, so it
 * naturally scrolls out of view along with the rest of the header once
 * the user scrolls down, with no JS visibility logic needed). Shows
 * the count of unread entries in the current ISO week (based on
 * firstSeenAt > localStorage[pp.currentWeekViewedAt]). Tapping it:
 *   1. Records "week viewed" in localStorage (via onView callback).
 *   2. Smooth-scrolls to the current-week section.
 */
export default function CurrentWeekChip({
  currentWeek,
  currentMonthKey,
  weekCount,
  unreadCount,
  onView,
}) {
  const handleClick = () => {
    if (!currentWeek) return;
    onView && onView();
    // Scrolls to the current MONTH section (MonthSection replaced the
    // old per-ISO-week sections; there's no longer a "week-<isoWeek>"
    // element to target). The current month auto-opens itself and its
    // default page is always the most recent week within it, so this
    // still lands the user on "this week" in effect.
    const el = currentMonthKey
      ? document.querySelector(`[data-testid="month-${currentMonthKey}"]`)
      : null;
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    else window.scrollTo({ top: 0, behavior: "smooth" });
  };

  if (!currentWeek) return null;

  const showUnread = typeof unreadCount === "number" && unreadCount > 0;

  return (
    <button
      type="button"
      onClick={handleClick}
      className={`pp-week-chip${showUnread ? " has-unread" : ""}`}
      data-testid="current-week-chip"
      data-unread={showUnread ? unreadCount : 0}
      aria-label={`Jump to current week ${currentWeek}` +
        (showUnread ? `, ${unreadCount} unread` : "")}
    >
      <span className="pp-week-chip-dot" aria-hidden />
      <span className="pp-week-chip-txt">
        This week
        {showUnread ? (
          <> · <strong data-testid="current-week-unread">{unreadCount}</strong> new</>
        ) : typeof weekCount === "number" ? (
          <> · <strong>{weekCount}</strong></>
        ) : null}
      </span>
    </button>
  );
}