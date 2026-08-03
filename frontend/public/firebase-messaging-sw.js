/* Placement Pulse — service worker.
 *   · Firebase Cloud Messaging background handler + click deep-link
 *   · App Badge increment on new background pushes
 *
 * NOTE: an earlier revision of this SW installed a full
 * stale-while-revalidate fetch handler for every same-origin GET.
 * Combined with CRA's dev-mode 2.4 MB bundle and Response.clone() it
 * pushed low-memory headless browsers over the edge. Offline READING is
 * now handled entirely via IndexedDB (src/lib/db.js) instead; the SW no
 * longer intercepts any fetch. Native HTTP cache handles the shell.
 */
/* eslint-disable no-undef, no-restricted-globals */

importScripts(
  "https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js",
);
importScripts(
  "https://www.gstatic.com/firebasejs/10.12.2/firebase-messaging-compat.js",
);

firebase.initializeApp({
  apiKey: "AIzaSyAiOJy3i5hKre3aMbji9G_L4FuGtDw7BJk",
  authDomain: "cec-placements23.firebaseapp.com",
  projectId: "cec-placements23",
  storageBucket: "cec-placements23.firebasestorage.app",
  messagingSenderId: "420062190823",
  appId: "1:420062190823:web:4be0242aaa53bc0283d642",
});

const messaging = firebase.messaging();
const BADGE_CACHE = "pp-badge-v1";

function targetPathFromPayload(payload) {
  const slno = payload?.data?.slno || "";
  const isBatch = payload?.data?.batch === "1";
  if (isBatch) return "/";
  if (slno && /^\d+$/.test(String(slno))) return `/entries/${slno}`;
  return "/";
}

async function bumpBadge(delta) {
  if (!("setAppBadge" in self.navigator)) return;
  try {
    const cache = await caches.open(BADGE_CACHE);
    const raw = await cache.match("badge-count");
    const cur = raw ? parseInt(await raw.text(), 10) || 0 : 0;
    const next = Math.max(0, cur + delta);
    await cache.put("badge-count", new Response(String(next)));
    if (next > 0) await self.navigator.setAppBadge(next);
    else await self.navigator.clearAppBadge?.();
  } catch (_) {
    /* progressive enhancement */
  }
}

messaging.onBackgroundMessage((payload) => {
  // Backend now sends data-only messages (see firebase_client.py) —
  // title/body always live under payload.data. The payload.notification
  // fallback is kept only in case a future message ever includes one.
  const title =
    payload?.notification?.title ||
    payload?.data?.title ||
    "New placement notice";
  const body =
    payload?.notification?.body || payload?.data?.body || "";
  const path = targetPathFromPayload(payload);

  bumpBadge(1);

  self.registration.showNotification(title, {
    body,
    icon: "/icons/icon-192.png",
    // Android status-bar badge must be a plain white silhouette on a
    // transparent background — a full-color icon here renders as a
    // blank/invisible shape once Android masks it. badge-96.png is
    // already built correctly for this; icon-192.png (detailed, full
    // color) was never the right asset for this field.
    badge: "/icons/badge-96.png",
    tag: `slno-${payload?.data?.slno || "0"}`,
    data: { path },
  });
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const path = event.notification?.data?.path || "/";

  event.waitUntil(
    (async () => {
      const allClients = await clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      for (const client of allClients) {
        try {
          const url = new URL(client.url);
          if (url.origin === self.location.origin) {
            await client.focus();
            if ("navigate" in client) return client.navigate(path);
            return client.postMessage({ type: "PP_NAVIGATE", path });
          }
        } catch (_) {
          /* ignore */
        }
      }
      return clients.openWindow(path);
    })(),
  );
});

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys.filter((k) => k !== BADGE_CACHE).map((k) => caches.delete(k)),
      );
      await self.clients.claim();
    })(),
  );
});

// No fetch handler — see file header.

self.addEventListener("message", (event) => {
  const msg = event.data;
  if (!msg || typeof msg !== "object") return;
  if (msg.type === "PP_CLEAR_BADGE") {
    (async () => {
      try {
        const cache = await caches.open(BADGE_CACHE);
        await cache.put("badge-count", new Response("0"));
        await self.navigator.clearAppBadge?.();
      } catch (_) {
        /* ignore */
      }
    })();
  }
});