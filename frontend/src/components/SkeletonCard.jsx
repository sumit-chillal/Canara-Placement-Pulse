/**
 * SkeletonCard — placeholder shown while the feed is still loading,
 * shaped like a real EntryCard so the layout doesn't jump once real
 * data arrives. Purely decorative (aria-hidden) since a screen reader
 * user gets no information from a shimmering box.
 */
export default function SkeletonCard() {
  return (
    <div className="pp-card pp-skeleton-card" aria-hidden="true">
      <div className="pp-skeleton pp-skeleton-meta" />
      <div className="pp-skeleton pp-skeleton-title" />
      <div className="pp-skeleton pp-skeleton-title pp-skeleton-title-short" />
      <div className="pp-skeleton-badges">
        <div className="pp-skeleton pp-skeleton-badge" />
        <div className="pp-skeleton pp-skeleton-badge" />
      </div>
      <div className="pp-skeleton pp-skeleton-line" />
      <div className="pp-skeleton pp-skeleton-line pp-skeleton-line-short" />
      <div className="pp-skeleton pp-skeleton-btn" />
    </div>
  );
}