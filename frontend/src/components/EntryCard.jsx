import { Link } from "react-router-dom";
import DOMPurify from "dompurify";

// Attachment links that are hosted on canaraengineering.in are proxied
// through our own backend (/api/files/...) so the browser never talks
// to the college's site directly. Those come back from the API as
// relative "/api/..." paths and need the backend origin prefixed.
// External application links (other domains) come back as full URLs
// already and pass through untouched.
const BACKEND_URL = process.env.REACT_APP_BACKEND_URL || "";

function resolveHref(url) {
  if (!url) return url;
  return url.startsWith("/api/") ? `${BACKEND_URL}${url}` : url;
}

function formatDate(iso) {
  try {
    return new Date(iso).toLocaleDateString("en-IN", {
      day: "2-digit",
      month: "short",
      year: "numeric",
    });
  } catch {
    return iso;
  }
}

function plainMessage(html, maxlen = 200) {
  const clean = DOMPurify.sanitize(html || "", { ALLOWED_TAGS: [] });
  const text = clean.replace(/\s+/g, " ").trim();
  return text.length > maxlen ? text.slice(0, maxlen - 1) + "…" : text;
}

const TYPE_LABELS = {
  drive:      "Drive",
  internship: "Internship",
  results:    "Results",
  ppt:        "PPT",
  notice:     "Notice",
};

function BranchBadges({ branches }) {
  if (!branches || branches.length === 0) return null;
  if (branches.includes("ALL")) {
    return (
      <span
        className="pp-badge pp-badge-branch pp-badge-all"
        data-testid="badge-branch-ALL"
      >
        All branches
      </span>
    );
  }
  const shown = branches.slice(0, 3);
  const rest = branches.length - shown.length;
  return (
    <>
      {shown.map((b) => (
        <span
          key={b}
          className="pp-badge pp-badge-branch"
          data-testid={`badge-branch-${b}`}
        >
          {b}
        </span>
      ))}
      {rest > 0 && (
        <span className="pp-badge pp-badge-branch pp-badge-more" data-testid="badge-branch-more">
          +{rest}
        </span>
      )}
    </>
  );
}

function BookmarkIcon({ filled }) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />
    </svg>
  );
}

export default function EntryCard({ entry, isUnread, isBookmarked, onToggleBookmark }) {
  const {
    slno,
    date,
    headline1,
    company,
    isMessageOnly,
    closingDate,
    registrationLinks = [],
    detailsHtml,
    ctc,
    branches,
    noticeType,
  } = entry;

  const typeLabel = TYPE_LABELS[noticeType] || null;

  return (
    <article
      className={`pp-card${isUnread ? " pp-card-unread" : ""}`}
      data-testid={`entry-card-${slno}`}
      data-notice-type={noticeType || "notice"}
    >
      {onToggleBookmark && (
        <button
          type="button"
          className={`pp-card-bookmark${isBookmarked ? " is-bookmarked" : ""}`}
          onClick={() => onToggleBookmark(entry)}
          aria-pressed={!!isBookmarked}
          aria-label={isBookmarked ? "Remove bookmark" : "Bookmark this notice"}
          title={isBookmarked ? "Remove bookmark" : "Bookmark this notice"}
          data-testid={`card-bookmark-${slno}`}
        >
          <BookmarkIcon filled={!!isBookmarked} />
        </button>
      )}

      <div className="pp-card-meta">
        <span className="pp-card-slno pp-mono" data-testid={`card-slno-${slno}`}>
          #{slno}
        </span>
        <span aria-hidden> · </span>
        <time dateTime={date}>{formatDate(date)}</time>
        {typeLabel && !isMessageOnly && (
          <span
            className={`pp-badge pp-badge-type pp-badge-type-${noticeType}`}
            data-testid={`badge-type-${slno}`}
          >
            {typeLabel}
          </span>
        )}
        {isUnread && (
          <span
            className="pp-card-newdot"
            aria-label="new"
            data-testid={`card-unread-${slno}`}
          />
        )}
      </div>

      {isMessageOnly ? (
        <>
          <h3
            className="pp-card-title"
            data-testid={`card-title-${slno}`}
          >
            Placement Department notice
          </h3>
          <p
            className="pp-card-message"
            data-testid={`card-message-${slno}`}
          >
            {entry.messageExcerpt || plainMessage(detailsHtml)}
          </p>
        </>
      ) : (
        <>
          {company && (
            <p
              className="pp-card-company"
              data-testid={`card-company-${slno}`}
            >
              {company}
            </p>
          )}
          <h3
            className="pp-card-title"
            data-testid={`card-title-${slno}`}
          >
            {headline1}
          </h3>

          {(ctc || (branches && branches.length > 0)) && (
            <div className="pp-card-badges" data-testid={`card-badges-${slno}`}>
              {ctc && (
                <span
                  className="pp-badge pp-badge-ctc"
                  data-testid={`badge-ctc-${slno}`}
                >
                  {ctc}
                </span>
              )}
              <BranchBadges branches={branches} />
            </div>
          )}

          {entry.messageExcerpt && (
            <p
              className="pp-card-excerpt"
              data-testid={`card-excerpt-${slno}`}
            >
              {entry.messageExcerpt}
            </p>
          )}

          {closingDate && (
            <p
              className="pp-card-deadline"
              data-testid={`card-deadline-${slno}`}
            >
              <span className="pp-card-deadline-icon" aria-hidden>
                ⏳
              </span>
              Registration closes on <strong>{closingDate}</strong>
            </p>
          )}
        </>
      )}

      <div className="pp-card-actions" data-testid={`card-actions-${slno}`}>
        {registrationLinks.length === 1 && (
          <a
            href={resolveHref(registrationLinks[0].url)}
            target="_blank"
            rel="noopener noreferrer"
            className="pp-btn pp-btn-solid pp-btn-sm"
            title={registrationLinks[0].label?.trim() || "Register Now"}
            data-testid={`card-btn-single-${slno}`}
          >
            {registrationLinks[0].label?.trim() || "Register Now"}
          </a>
        )}
        {registrationLinks.length > 1 && (
          <div
            className="pp-btn-group"
            data-testid={`card-btn-group-${slno}`}
          >
            {registrationLinks.map((l, i) => (
              <a
                key={`${l.url}-${i}`}
                href={resolveHref(l.url)}
                target="_blank"
                rel="noopener noreferrer"
                className="pp-btn pp-btn-ghost pp-btn-sm"
                title={l.label?.trim() || `Link ${i + 1}`}
                data-testid={`card-btn-${slno}-${i}`}
              >
                {l.label?.trim() || `Link ${i + 1}`}
              </a>
            ))}
          </div>
        )}
        <Link
          to={`/entries/${slno}`}
          className="pp-btn pp-btn-link pp-btn-sm"
          data-testid={`card-details-${slno}`}
        >
          Full details →
        </Link>
      </div>
    </article>
  );
}