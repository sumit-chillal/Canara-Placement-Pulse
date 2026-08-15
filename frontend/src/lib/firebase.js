/* Firebase Web + FCM client for Placement Pulse.
 *
 *   subscribeToPlacementUpdates()
 *     - asks for Notification permission
 *     - registers the SW at /firebase-messaging-sw.js
 *     - obtains an FCM token (VAPID)
 *     - POSTs the token to /api/subscribe (which server-side binds it
 *       to the "placement-updates" topic)
 *     - returns { ok, token, error }
 *
 *   unsubscribeFromPlacementUpdates(token)
 *     - POSTs the token to /api/unsubscribe (best-effort — failure here
 *       doesn't block the local step below)
 *     - deletes the local FCM token via deleteToken(), which is what
 *       actually guarantees this device stops receiving pushes,
 *       independent of whether the server-side call succeeded
 *     - returns { ok, error }
 *
 *   listenForForegroundMessages(cb) — for in-app toasts while the tab
 *     is focused.
 */
import { initializeApp, getApps, getApp } from "firebase/app";
import {
  getMessaging,
  getToken,
  deleteToken,
  isSupported,
  onMessage,
} from "firebase/messaging";
import axios from "axios";

// Local/dev-only: free ngrok tunnel domains show an interstitial
// "you're about to visit..." warning page to any request that isn't a
// direct browser navigation the visitor has already clicked through.
// Cross-origin XHR/fetch calls (everything this app makes, since the
// frontend and backend live on different domains) never get that
// click-through, so ngrok serves its warning HTML instead of proxying
// to the real API — which has no CORS headers, so the browser reports
// it as a CORS failure even though the actual cause is ngrok's own
// gate, not the backend's CORS config. This header tells ngrok to
// skip the interstitial. It's ignored (harmless) by any non-ngrok
// host, so nothing needs to change here once you move to real hosting.
axios.defaults.headers.common["ngrok-skip-browser-warning"] = "true";

const firebaseConfig = {
  apiKey: process.env.REACT_APP_FIREBASE_API_KEY,
  authDomain: process.env.REACT_APP_FIREBASE_AUTH_DOMAIN,
  projectId: process.env.REACT_APP_FIREBASE_PROJECT_ID,
  storageBucket: process.env.REACT_APP_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: process.env.REACT_APP_FIREBASE_MESSAGING_SENDER_ID,
  appId: process.env.REACT_APP_FIREBASE_APP_ID,
  measurementId: process.env.REACT_APP_FIREBASE_MEASUREMENT_ID,
};

const VAPID_KEY = process.env.REACT_APP_FIREBASE_VAPID_KEY;
const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

export function getFirebaseApp() {
  return getApps().length ? getApp() : initializeApp(firebaseConfig);
}

async function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) {
    throw new Error("Service workers are not supported in this browser.");
  }
  // Prefer any existing registration; otherwise register ours.
  const existing = await navigator.serviceWorker.getRegistration(
    "/firebase-messaging-sw.js",
  );
  const registration =
    existing || (await navigator.serviceWorker.register("/firebase-messaging-sw.js"));

  // `navigator.serviceWorker.ready` resolves once a service worker for
  // this scope is genuinely ACTIVE — the real precondition a push
  // subscribe needs, and the browser's own guaranteed signal for it.
  // The previous approach here hand-rolled a wait on
  // registration.installing/.waiting with a 4s fallback timeout that
  // resolved regardless of actual state — iOS Safari activates a
  // fresh service worker noticeably slower than Chrome/Android, so
  // that timeout could fire before the worker was really active,
  // letting getToken() run against a not-yet-active registration.
  // That's exactly what surfaces on iOS as the native error
  // "Subscribing for push requires an active service worker."
  await navigator.serviceWorker.ready;
  return registration;
}

// Apple's own Web Push provisioning backend is known to intermittently
// fail on the very first subscribe attempt right after a service
// worker activates ("Failed due to internal service error" on iOS),
// then succeed a moment later on retry. One short, silent retry here
// absorbs that without making the student tap the bell twice.
async function getTokenWithRetry(messaging, options, attempts = 2) {
  let lastErr;
  for (let i = 0; i < attempts; i += 1) {
    try {
      return await getToken(messaging, options);
    } catch (err) {
      lastErr = err;
      if (i < attempts - 1) {
        await new Promise((resolve) => setTimeout(resolve, 1200));
      }
    }
  }
  throw lastErr;
}

export async function subscribeToPlacementUpdates(branches = []) {
  try {
    if (!(await isSupported())) {
      return { ok: false, error: "This browser doesn't support web push (e.g. Safari private mode)." };
    }
    if (!("Notification" in window)) {
      return { ok: false, error: "Notifications API missing in this browser." };
    }
    if (!("PushManager" in window)) {
      return {
        ok: false,
        error: "Push API unavailable — this happens in incognito windows and some in-app browsers. Open the site in a regular tab.",
      };
    }

    const permission = await Notification.requestPermission();
    if (permission === "denied") {
      return {
        ok: false,
        error: "Notifications are blocked for this site. Unblock in your browser's site settings, then reload.",
      };
    }
    if (permission !== "granted") {
      return { ok: false, error: `Notification permission: ${permission}` };
    }

    // registerServiceWorker() now internally awaits SW readiness — see
    // the comment there for why this matters specifically on iOS.
    const registration = await registerServiceWorker();

    const messaging = getMessaging(getFirebaseApp());
    const token = await getTokenWithRetry(messaging, {
      vapidKey: VAPID_KEY,
      serviceWorkerRegistration: registration,
    });
    if (!token) {
      return { ok: false, error: "FCM did not return a token." };
    }

    const res = await axios.post(`${BACKEND_URL}/api/subscribe`, { token, branches });
    if (!res.data?.subscribed) {
      return {
        ok: false,
        error: `Backend refused subscription: ${JSON.stringify(res.data)}`,
        token,
      };
    }
    return { ok: true, token };
  } catch (err) {
    return { ok: false, error: err?.message || String(err) };
  }
}

export async function unsubscribeFromPlacementUpdates(token, branches = []) {
  try {
    // Best-effort: tell the backend to drop this token from the topic.
    // Even if this fails (network blip, token already stale server-side,
    // etc.) we still delete the local token below regardless — that's
    // what actually stops pushes reaching this device, independent of
    // the topic-membership bookkeeping on the server.
    if (token) {
      try {
        await axios.post(`${BACKEND_URL}/api/unsubscribe`, { token, branches });
      } catch (_) {
        /* ignore — local deleteToken below is what actually matters */
      }
    }

    if (await isSupported()) {
      const messaging = getMessaging(getFirebaseApp());
      await deleteToken(messaging);
    }
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err?.message || String(err) };
  }
}

/**
 * Reconciles this device's branch-topic subscriptions to exactly
 * `branches`, without touching the base ALL-branches topic and
 * without deleting/rotating the local FCM token — lets the
 * notification bell's "edit branches" flow change preference in
 * place instead of a full unsubscribe+resubscribe cycle.
 */
export async function updateNotificationBranches(token, branches = []) {
  try {
    if (!token) {
      return { ok: false, error: "No active subscription to update." };
    }
    const res = await axios.post(`${BACKEND_URL}/api/subscribe/branches`, {
      token,
      branches,
    });
    if (!res.data?.updated) {
      return {
        ok: false,
        error: `Backend refused update: ${JSON.stringify(res.data)}`,
      };
    }
    return { ok: true, branches: res.data.branches || branches };
  } catch (err) {
    return { ok: false, error: err?.message || String(err) };
  }
}

export function listenForForegroundMessages(cb) {
  isSupported().then((supported) => {
    if (!supported) return;
    const messaging = getMessaging(getFirebaseApp());
    onMessage(messaging, (payload) => cb(payload));
  });
}

export function currentPermission() {
  if (typeof Notification === "undefined") return "unsupported";
  return Notification.permission;
}