import { useState } from "react";
import EntryCard from "@/components/EntryCard";

function weekLabel(isoWeek) {
  if (!isoWeek) return "Undated";
  const [year, w] = isoWeek.split("-W");
  return `Week ${parseInt(w, 10)} · ${year}`;
}

// Same sessionStorage persistence pattern as MonthSection.jsx — see
// that file's comment for why this is needed alongside the scroll-
// position restore in Home.jsx, not instead of it.
function readStoredOpen(key, fallback) {
  try {
    const raw = sessionStorage.getItem(key);
    if (raw === "1") return true;
    if (raw === "0") return false;
  } catch (_) {
    /* ignore */
  }
  return fallback;
}

export default function WeekSection({
  isoWeek,
  entries,
  defaultOpen = false,
  lastSeenSlno = 0,
  currentWeek = null,
  weekViewedAt = null,
}) {
  const testId = isoWeek || "undated";
  const storageKey = `pp-week-open-${testId}`;

  // Auto-open the current week so students see the freshest notices
  // without one more tap — but only as the fallback when there's no
  // stored preference yet; an explicit prior open/close by the user
  // always wins on return.
  const [open, setOpenState] = useState(() =>
    readStoredOpen(storageKey, defaultOpen || isoWeek === currentWeek),
  );
  const setOpen = (updater) => {
    setOpenState((prev) => {
      const next = typeof updater === "function" ? updater(prev) : updater;
      try {
        sessionStorage.setItem(storageKey, next ? "1" : "0");
      } catch (_) {
        /* ignore */
      }
      return next;
    });
  };

  const isCurrent = isoWeek === currentWeek;
  const cutoff = weekViewedAt ? new Date(weekViewedAt).getTime() : null;
  return (
    <section
      className={`pp-week${open ? " is-open" : ""}${isCurrent ? " is-current" : ""}`}
      data-testid={`week-${testId}`}
    >
      <button
        type="button"
        className="pp-week-header"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        data-testid={`week-toggle-${testId}`}
      >
        <span className="pp-week-label">
          {weekLabel(isoWeek)}
          {isCurrent && (
            <span
              className="pp-week-badge-current"
              data-testid={`week-badge-current-${testId}`}
            >
              Current
            </span>
          )}
        </span>
        <span className="pp-week-count">
          {entries.length} {entries.length === 1 ? "notice" : "notices"}
        </span>
        <span
          className={`pp-week-caret${open ? " is-open" : ""}`}
          aria-hidden
        >
          ▾
        </span>
      </button>
      {open && (
        <div
          className="pp-week-body"
          data-testid={`week-body-${testId}`}
        >
          {entries.map((e) => {
            const seenBySlno = e.slno > lastSeenSlno;
            const seenByWeek =
              isCurrent && cutoff && e.firstSeenAt &&
              new Date(e.firstSeenAt).getTime() > cutoff;
            return (
              <EntryCard
                key={e.slno}
                entry={e}
                isUnread={seenBySlno || seenByWeek}
              />
            );
          })}
        </div>
      )}
    </section>
  );
}