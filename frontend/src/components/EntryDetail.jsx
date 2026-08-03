import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import axios from "axios";
import DOMPurify from "dompurify";
import { cacheDetail, getCachedDetail } from "@/lib/db";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL || "";
const API = `${BACKEND_URL}/api`;

// Same proxy-resolution rule as EntryCard.jsx: "/api/..." paths point
// at our own backend's college-file proxy; anything else is already a
// full external URL and is used as-is.
function resolveHref(url) {
  if (!url) return url;
  return url.startsWith("/api/") ? `${BACKEND_URL}${url}` : url;
}

function formatDate(iso) {
  try {
    return new Date(iso).toLocaleDateString("en-IN", {
      day: "2-digit",
      month: "long",
      year: "numeric",
    });
  } catch {
    return iso;
  }
}

export default function EntryDetail() {
  const { slno } = useParams();
  const [entry, setEntry] = useState(null);
  const [error, setError] = useState(null);
  const [status, setStatus] = useState("loading");
  const [source, setSource] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      // 1) Try cache first — instant paint.
      try {
        const cached = await getCachedDetail(slno);
        if (!cancelled && cached) {
          setEntry(cached);
          setStatus("ok");
          setSource("cache");
        }
      } catch (_) {
        /* ignore */
      }
      // 2) Refresh from network in the background.
      try {
        const r = await axios.get(`${API}/entries/${slno}`);
        if (cancelled) return;
        setEntry(r.data);
        setStatus("ok");
        setSource("network");
        cacheDetail(r.data);
      } catch (e) {
        if (cancelled) return;
        // Only show error if we don't have cache to render.
        if (!entry) {
          setError(
            e.response?.status === 404
              ? `No notice with slno=${slno}`
              : e.message,
          );
          setStatus("error");
        } else {
          setSource("offline");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slno]);

  const cleanHtml = entry
    ? DOMPurify.sanitize(entry.detailsHtml || "", {
        ALLOWED_TAGS: [
          "p", "br", "b", "strong", "i", "em", "u", "s", "sup", "sub",
          "ul", "ol", "li", "a", "span", "div",
          "table", "thead", "tbody", "tr", "th", "td",
          "h1", "h2", "h3", "h4", "h5", "h6",
          "blockquote", "pre", "code", "hr", "img",
        ],
        ALLOWED_ATTR: [
          "href", "target", "rel", "colspan", "rowspan",
          "src", "alt", "width", "height",
        ],
      })
    : "";

  return (
    <main className="pp-shell" data-testid="detail-shell">
      <div className="pp-container pp-container-detail">
        <Link to="/" className="pp-back" data-testid="back-link">
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <line x1="19" y1="12" x2="5" y2="12" />
            <polyline points="12 19 5 12 12 5" />
          </svg>
          Back
        </Link>

        {status === "loading" && (
          <p className="pp-empty" data-testid="detail-loading">
            Loading&hellip;
          </p>
        )}

        {status === "error" && (
          <div className="pp-empty" data-testid="detail-error">
            {error}
          </div>
        )}

        {status === "ok" && entry && (
          <article className="pp-detail" data-testid="detail-article">
            {source === "offline" && (
              <span
                className="pp-chip pp-chip-warn"
                data-testid="detail-offline"
              >
                Offline · showing cached notice
              </span>
            )}

            <p className="pp-eyebrow" data-testid="detail-slno">
              Notice #{entry.slno} · {entry.isoWeek}
            </p>
            <h1 className="pp-title pp-title-detail" data-testid="detail-title">
              {entry.headline1}
            </h1>
            <p className="pp-meta" data-testid="detail-meta">
              {entry.company ? (
                <>
                  <span data-testid="detail-company">{entry.company}</span> ·{" "}
                </>
              ) : null}
              <span data-testid="detail-date">{formatDate(entry.date)}</span>
              {entry.closingDate && (
                <>
                  {" "}·{" "}
                  <span data-testid="detail-deadline">
                    Registration closes on <strong>{entry.closingDate}</strong>
                  </span>
                </>
              )}
            </p>

            <div
              className="pp-html"
              data-testid="detail-html"
              dangerouslySetInnerHTML={{ __html: cleanHtml }}
            />

            {entry.registrationLinks?.length > 0 && (
              <section className="pp-links" data-testid="detail-links">
                <h3 className="pp-section-heading">Attachments &amp; links</h3>
                <ul className="pp-links-list">
                  {entry.registrationLinks.map((l, i) => (
                    <li key={`${l.url}-${i}`}>
                      <a
                        href={resolveHref(l.url)}
                        target="_blank"
                        rel="noopener noreferrer"
                        data-testid={`detail-link-${i}`}
                      >
                        {l.label || l.url}
                      </a>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </article>
        )}
      </div>
    </main>
  );
}