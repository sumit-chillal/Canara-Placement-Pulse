/* Detection + badging helpers for the PWA install experience. */

export function isStandalone() {
  if (typeof window === "undefined") return false;
  const mm = window.matchMedia && window.matchMedia("(display-mode: standalone)");
  return (mm && mm.matches) || window.navigator.standalone === true;
}

export function isIosSafari() {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent || "";
  const isIos = /iPad|iPhone|iPod/.test(ua) && !window.MSStream;
  // iPadOS 13+ reports as MacIntel with touch — catch that too.
  const isIpadOS =
    /Macintosh/.test(ua) &&
    "ontouchend" in document &&
    navigator.maxTouchPoints > 1;
  if (!(isIos || isIpadOS)) return false;
  // Exclude in-app browsers (Instagram, Facebook, TikTok) — they can't install.
  if (/(FBAN|FBAV|Instagram|Line|TikTok|Twitter)/i.test(ua)) return false;
  // Chrome-on-iOS, Firefox-on-iOS use WebKit but can't A2HS the same way;
  // check for Safari specifically.
  return /Safari/.test(ua) && !/CriOS|FxiOS|EdgiOS|OPT\//.test(ua);
}

export function hasBadgingApi() {
  return (
    typeof navigator !== "undefined" && typeof navigator.setAppBadge === "function"
  );
}

export async function clearBadge() {
  try {
    if (typeof navigator !== "undefined" && navigator.clearAppBadge) {
      await navigator.clearAppBadge();
    }
    // Also nudge the SW to reset its persisted counter.
    if ("serviceWorker" in navigator) {
      const reg = await navigator.serviceWorker.getRegistration();
      reg?.active?.postMessage({ type: "PP_CLEAR_BADGE" });
    }
  } catch (_) {
    /* progressive enhancement */
  }
}
