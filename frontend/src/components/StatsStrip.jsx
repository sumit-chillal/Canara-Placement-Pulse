import { useEffect, useState } from "react";
import axios from "axios";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

function fmtRelative(iso) {
  if (!iso) return null;
  try {
    const then = new Date(iso).getTime();
    const now = Date.now();
    const diffMin = Math.round((now - then) / 60000);
    if (diffMin < 2) return "just now";
    if (diffMin < 60) return `${diffMin} min ago`;
    const h = Math.round(diffMin / 60);
    if (h < 24) return `${h} h ago`;
    const d = Math.round(h / 24);
    return `${d} d ago`;
  } catch {
    return null;
  }
}

export default function StatsStrip() {
  const [stats, setStats] = useState(null);

  useEffect(() => {
    let cancelled = false;
    axios.get(`${API}/stats`).then((r) => {
      if (!cancelled) setStats(r.data);
    }).catch(() => {
      /* stats is progressive — silent failure OK, shimmer just stays up */
    });
    return () => { cancelled = true; };
  }, []);

  if (!stats) {
    // Visible shimmer instead of an invisible placeholder — on a slow
    // connection the old version just left a blank gap here, which
    // read as "missing" rather than "loading".
    return (
      <div className="pp-stats-strip pp-stats-strip-loading" data-testid="stats-loading" aria-hidden="true">
        <div className="pp-skeleton pp-skeleton-stat" />
        <div className="pp-skeleton pp-skeleton-stat" />
        <div className="pp-skeleton pp-skeleton-stat" />
      </div>
    );
  }

  const lastPoll = stats.lastPoll?.finished_at;

  return (
    <div className="pp-stats-strip" data-testid="stats-strip">
      <div className="pp-stat">
        <span className="pp-stat-num" data-testid="stat-total">{stats.totalNotices}</span>
        <span className="pp-stat-lbl">Total drives</span>
      </div>
      <div className="pp-stat">
        <span className="pp-stat-num" data-testid="stat-week">{stats.thisWeekCount}</span>
        <span className="pp-stat-lbl">This week</span>
      </div>
      <div className="pp-stat">
        <span className="pp-stat-num" data-testid="stat-companies">{stats.uniqueCompanies}</span>
        <span className="pp-stat-lbl">Companies</span>
      </div>
      {lastPoll && (
        <div className="pp-stat pp-stat-poll" data-testid="stat-lastpoll">
          <span className="pp-stat-lbl">Last synced</span>
          <span className="pp-stat-poll-val">{fmtRelative(lastPoll)}</span>
        </div>
      )}
    </div>
  );
}