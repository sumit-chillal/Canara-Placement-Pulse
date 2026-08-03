import { useState } from "react";
import EntryCard from "@/components/EntryCard";

function weekLabel(isoWeek) {
  if (!isoWeek) return "Undated";
  const [year, w] = isoWeek.split("-W");
  return `Week ${parseInt(w, 10)} · ${year}`;
}

export default function WeekSection({
  isoWeek,
  entries,
  defaultOpen = false,
  lastSeenSlno = 0,
  currentWeek = null,
  weekViewedAt = null,
}) {
  // Auto-open the current week so students see the freshest notices
  // without one more tap.
  const [open, setOpen] = useState(defaultOpen || isoWeek === currentWeek);
  const testId = isoWeek || "undated";
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
