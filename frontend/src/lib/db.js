/* IndexedDB cache — powers instant offline loads.
 *
 * `entries`   object store keyed by slno holds the full notice payload.
 * `meta`      store keeps `lastSeenSlno` for the unread badge and
 *             `lastSyncedAt` for the offline chip.
 * `bookmarks` store keyed by slno holds {slno, bookmarkedAt} — just a
 *             reference + timestamp, not a duplicate copy of the full
 *             notice. Full details for a bookmarked entry are looked
 *             up from `entries`, which is never pruned locally even
 *             after a notice ages out of the server's 100-item cap —
 *             so a bookmark keeps working (from cache) even after the
 *             live notice is gone server-side.
 */
import { openDB } from "idb";

const DB_NAME = "placement-pulse";
const DB_VERSION = 2;

let _dbPromise = null;
function db() {
  if (!_dbPromise) {
    _dbPromise = openDB(DB_NAME, DB_VERSION, {
      upgrade(d) {
        if (!d.objectStoreNames.contains("entries")) {
          d.createObjectStore("entries", { keyPath: "slno" });
        }
        if (!d.objectStoreNames.contains("meta")) {
          d.createObjectStore("meta");
        }
        if (!d.objectStoreNames.contains("bookmarks")) {
          d.createObjectStore("bookmarks", { keyPath: "slno" });
        }
      },
    });
  }
  return _dbPromise;
}

export async function cacheEntries(entries) {
  const d = await db();
  // Chunk to avoid stalling the main thread on large writes.
  const CHUNK = 40;
  for (let i = 0; i < entries.length; i += CHUNK) {
    const slice = entries.slice(i, i + CHUNK);
    const tx = d.transaction("entries", "readwrite");
    for (const e of slice) tx.store.put(e);
    await tx.done;
    // Yield to the event loop between chunks.
    await new Promise((r) => setTimeout(r, 0));
  }
  await d.put("meta", new Date().toISOString(), "lastSyncedAt");
}

export async function loadCachedEntries() {
  const d = await db();
  return d.getAll("entries");
}

export async function getLastSyncedAt() {
  const d = await db();
  return d.get("meta", "lastSyncedAt");
}

export async function getLastSeenSlno() {
  const d = await db();
  return (await d.get("meta", "lastSeenSlno")) || 0;
}

export async function setLastSeenSlno(slno) {
  const d = await db();
  await d.put("meta", slno, "lastSeenSlno");
}

export async function cacheDetail(entry) {
  if (!entry?.slno) return;
  const d = await db();
  await d.put("entries", entry);
}

export async function getCachedDetail(slno) {
  const d = await db();
  return d.get("entries", Number(slno));
}

// --- Bookmarks -------------------------------------------------------

export async function addBookmark(slno) {
  if (!slno) return;
  const d = await db();
  await d.put("bookmarks", { slno: Number(slno), bookmarkedAt: new Date().toISOString() });
}

export async function removeBookmark(slno) {
  if (!slno) return;
  const d = await db();
  await d.delete("bookmarks", Number(slno));
}

export async function isBookmarked(slno) {
  const d = await db();
  const rec = await d.get("bookmarks", Number(slno));
  return !!rec;
}

/** Returns a Set of bookmarked slnos — cheap single read for building
 * the "is this card bookmarked" lookup used across a whole list. */
export async function getAllBookmarkSlnos() {
  const d = await db();
  const all = await d.getAll("bookmarks");
  return new Set(all.map((r) => r.slno));
}

/**
 * Full bookmarked entries, newest-bookmarked first, joined against the
 * `entries` cache. A bookmark whose entry somehow isn't in the local
 * cache (shouldn't normally happen — you can only bookmark something
 * you've already seen rendered, which means it was already cached) is
 * silently skipped rather than showing a broken card.
 */
export async function getBookmarkedEntries() {
  const d = await db();
  const marks = await d.getAll("bookmarks");
  marks.sort((a, b) => (b.bookmarkedAt || "").localeCompare(a.bookmarkedAt || ""));
  const out = [];
  for (const m of marks) {
    const entry = await d.get("entries", m.slno);
    if (entry) out.push(entry);
  }
  return out;
}