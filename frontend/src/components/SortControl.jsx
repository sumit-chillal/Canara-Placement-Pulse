const SORT_OPTIONS = [
  { value: "newest", label: "Newest posted" },
  { value: "oldest", label: "Oldest posted" },
  { value: "company", label: "Company A–Z" },
  { value: "recent", label: "Recently added" },
];

export default function SortControl({ value, onChange }) {
  return (
    <div className="pp-sort" data-testid="sort-control">
      <label htmlFor="pp-sort-select" className="pp-sort-label">
        Sort
      </label>
      <select
        id="pp-sort-select"
        className="pp-sort-select"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        data-testid="sort-select"
      >
        {SORT_OPTIONS.map((o) => (
          <option key={o.value} value={o.value} data-testid={`sort-opt-${o.value}`}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}

export function applySort(entries, mode) {
  const arr = [...entries];
  switch (mode) {
    case "oldest":
      arr.sort((a, b) => (a.date > b.date ? 1 : a.date < b.date ? -1 : a.slno - b.slno));
      break;
    case "company":
      arr.sort((a, b) => {
        const ca = (a.company || "\uffff").toLowerCase();
        const cb = (b.company || "\uffff").toLowerCase();
        if (ca !== cb) return ca < cb ? -1 : 1;
        return (b.date || "").localeCompare(a.date || "");
      });
      break;
    case "recent":
      arr.sort((a, b) => {
        const fa = a.firstSeenAt || a.date || "";
        const fb = b.firstSeenAt || b.date || "";
        return fb.localeCompare(fa);
      });
      break;
    case "newest":
    default:
      arr.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : b.slno - a.slno));
      break;
  }
  return arr;
}
