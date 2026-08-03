import { useCallback, useEffect, useRef, useState } from "react";
import {
  subscribeToPlacementUpdates,
  unsubscribeFromPlacementUpdates,
  updateNotificationBranches,
  currentPermission,
} from "@/lib/firebase";
import { isIosSafari, isStandalone } from "@/lib/pwa";

const TOKEN_KEY = "pp-fcm-token";
const SUBSCRIBED_KEY = "pp-notif-subscribed";
const BRANCHES_KEY = "pp-notif-branches";

// Same branch codes used everywhere else in the app (FilterBar,
// ingest extractor, server-side allowlist).
const BRANCH_ORDER = ["CSE", "ISE", "IT", "ECE", "EEE", "ME", "CV", "AIML", "DS", "MBA", "MCA"];

function getStoredToken() {
  try {
    return window.localStorage.getItem(TOKEN_KEY) || null;
  } catch {
    return null;
  }
}

function setStoredToken(token) {
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

function getStoredSubscribed() {
  try {
    return window.localStorage.getItem(SUBSCRIBED_KEY) === "1";
  } catch {
    return false;
  }
}

function setStoredSubscribed(v) {
  try {
    window.localStorage.setItem(SUBSCRIBED_KEY, v ? "1" : "0");
  } catch {
    /* ignore */
  }
}

function getStoredBranches() {
  try {
    const raw = window.localStorage.getItem(BRANCHES_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function setStoredBranches(branches) {
  try {
    window.localStorage.setItem(BRANCHES_KEY, JSON.stringify(branches || []));
  } catch {
    /* ignore */
  }
}

function BellIcon({ muted }) {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
      <path d="M13.73 21a2 2 0 0 1-3.46 0" />
      {muted && <line x1="3" y1="3" x2="21" y2="21" />}
    </svg>
  );
}

/**
 * Bell button that toggles this device's push subscription without
 * sending the user to browser/app settings, with branch-specific
 * targeting (see server.py's VALID_BRANCH_CODES / branch topics) that
 * can be edited in place after subscribing:
 *
 *  - OFF → click: opens the branch picker ("which branches do you
 *    want notices for?"), subscribes with that selection on confirm.
 *    Every subscriber always gets ALL-branches notices regardless of
 *    what's picked here — this only adds narrower branch-specific
 *    ones on top.
 *  - ON  → click: opens a manage panel showing current branches, with
 *    "Edit branches" (reopens the picker pre-filled with the current
 *    selection, updates in place via /api/subscribe/branches — no
 *    token rotation, no re-permission-prompt) and "Turn off
 *    notifications" (the actual unsubscribe).
 *  - iOS Safari that hasn't been added to the Home Screen gets an
 *    explanatory popover instead — Apple only allows push for
 *    installed PWAs.
 */
export default function NotificationBell() {
  const [subscribed, setSubscribed] = useState(getStoredSubscribed);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [showConfirm, setShowConfirm] = useState(false);
  const [needsIosInstall, setNeedsIosInstall] = useState(false);
  const [panel, setPanel] = useState(null); // null | "manage" | "picker"
  const [pickedBranches, setPickedBranches] = useState(getStoredBranches);
  const [pickerMode, setPickerMode] = useState(
    getStoredBranches().length > 0 ? "custom" : "all",
  );
  const [pickerError, setPickerError] = useState(null);
  const wrapRef = useRef(null);

  // If the user previously subscribed but has since revoked the
  // browser-level permission from site settings, don't show a stale
  // "on" bell — permission is the one thing we can't control from here.
  useEffect(() => {
    if (getStoredSubscribed() && currentPermission() !== "granted") {
      setSubscribed(false);
      setStoredSubscribed(false);
      setStoredToken(null);
    }
  }, []);

  useEffect(() => {
    if (!showConfirm) return undefined;
    const t = setTimeout(() => setShowConfirm(false), 5000);
    return () => clearTimeout(t);
  }, [showConfirm]);

  // Close any open panel on outside click.
  useEffect(() => {
    if (!panel) return undefined;
    const onDocClick = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) {
        setPanel(null);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [panel]);

  const toggleBranch = (code) => {
    setPickedBranches((cur) => {
      const set = new Set(cur);
      if (set.has(code)) set.delete(code);
      else set.add(code);
      return Array.from(set);
    });
  };

  const doSubscribe = useCallback(async () => {
    if (pickerMode === "custom" && pickedBranches.length === 0) {
      setPickerError("Select at least one branch, or choose \u201cAll branches\u201d instead.");
      return;
    }
    setPickerError(null);
    if (isIosSafari() && !isStandalone()) {
      setNeedsIosInstall(true);
      setPanel(null);
      return;
    }
    setNeedsIosInstall(false);
    const branchesToSend = pickerMode === "all" ? [] : pickedBranches;
    setBusy(true);
    const res = await subscribeToPlacementUpdates(branchesToSend);
    setBusy(false);
    if (res.ok) {
      setSubscribed(true);
      setStoredSubscribed(true);
      setStoredToken(res.token);
      setStoredBranches(branchesToSend);
      setPanel(null);
      setShowConfirm(true);
    } else {
      setError(res.error || "Couldn't enable notifications.");
    }
  }, [pickedBranches, pickerMode]);

  const doUpdateBranches = useCallback(async () => {
    if (pickerMode === "custom" && pickedBranches.length === 0) {
      setPickerError("Select at least one branch, or choose \u201cAll branches\u201d instead.");
      return;
    }
    setPickerError(null);
    const branchesToSend = pickerMode === "all" ? [] : pickedBranches;
    const token = getStoredToken();
    setBusy(true);
    const res = await updateNotificationBranches(token, branchesToSend);
    setBusy(false);
    if (res.ok) {
      setStoredBranches(branchesToSend);
      setPanel(null);
    } else {
      setError(res.error || "Couldn't update branches.");
    }
  }, [pickedBranches, pickerMode]);

  const doUnsubscribe = useCallback(async () => {
    setBusy(true);
    const token = getStoredToken();
    const branches = getStoredBranches();
    const res = await unsubscribeFromPlacementUpdates(token, branches);
    setBusy(false);
    setSubscribed(false);
    setStoredSubscribed(false);
    setStoredToken(null);
    setPanel(null);
    if (!res.ok && res.error) setError(res.error);
  }, []);

  const openPickerFresh = () => {
    const stored = getStoredBranches();
    setPickedBranches(stored);
    setPickerMode(stored.length > 0 ? "custom" : "all");
    setPickerError(null);
    setPanel((p) => (p === "picker" ? null : "picker"));
  };

  const handleBellClick = useCallback(() => {
    if (busy) return;
    setError(null);

    if (isIosSafari() && !isStandalone()) {
      setNeedsIosInstall(true);
      return;
    }

    if (subscribed) {
      // Open the "manage" panel (edit branches / turn off) instead of
      // unsubscribing immediately.
      setPanel((p) => (p === "manage" ? null : "manage"));
    } else {
      openPickerFresh();
    }
  }, [busy, subscribed]);

  const openEditPicker = () => {
    const stored = getStoredBranches();
    setPickedBranches(stored);
    setPickerMode(stored.length > 0 ? "custom" : "all");
    setPickerError(null);
    setPanel("picker");
  };

  const label = subscribed
    ? "Notifications on — tap to manage"
    : "Turn on notifications";

  const currentBranchesLabel =
    getStoredBranches().length > 0 ? getStoredBranches().join(", ") : "All branches only";

  return (
    <div className="pp-bell-wrap" ref={wrapRef}>
      <button
        type="button"
        className={`pp-bell${subscribed ? " is-on" : ""}${busy ? " is-busy" : ""}`}
        onClick={handleBellClick}
        aria-pressed={subscribed}
        aria-label={label}
        title={label}
        data-testid="notification-bell"
        disabled={busy}
      >
        <BellIcon muted={!subscribed} />
      </button>

      {panel === "manage" && (
        <div
          className="pp-bell-popover"
          data-testid="bell-manage-panel"
          role="dialog"
          aria-label="Manage notifications"
        >
          <p>
            Notifications on. Currently getting:{" "}
            <b>{currentBranchesLabel}</b>.
          </p>
          <div className="pp-bell-manage-actions">
            <button
              type="button"
              className="pp-btn pp-btn-ghost pp-btn-sm"
              onClick={openEditPicker}
              data-testid="bell-edit-branches"
            >
              Edit branches
            </button>
            <button
              type="button"
              className="pp-btn-link"
              onClick={doUnsubscribe}
              disabled={busy}
              data-testid="bell-turn-off"
            >
              Turn off notifications
            </button>
          </div>
        </div>
      )}

      {panel === "picker" && (
        <div
          className="pp-bell-popover pp-branch-picker"
          data-testid="bell-branch-picker"
          role="dialog"
          aria-label="Choose branches for notifications"
        >
          <p>
            You&rsquo;ll always get notices tagged for <b>all branches</b>.
            Choose how narrow you want it:
          </p>

          <div className="pp-picker-mode-row" role="radiogroup" aria-label="Notification scope">
            <label className="pp-picker-mode-option">
              <input
                type="radio"
                name="pp-picker-mode"
                checked={pickerMode === "all"}
                onChange={() => {
                  setPickerMode("all");
                  setPickerError(null);
                }}
                data-testid="bell-mode-all"
              />
              All branches
            </label>
            <label className="pp-picker-mode-option">
              <input
                type="radio"
                name="pp-picker-mode"
                checked={pickerMode === "custom"}
                onChange={() => {
                  setPickerMode("custom");
                  setPickerError(null);
                }}
                data-testid="bell-mode-custom"
              />
              Customise branches
            </label>
          </div>

          {pickerMode === "custom" && (
            <div className="pp-branch-picker-grid">
              {BRANCH_ORDER.map((code) => {
                const active = pickedBranches.includes(code);
                return (
                  <label key={code} className="pp-branch-option">
                    <input
                      type="checkbox"
                      checked={active}
                      onChange={() => {
                        toggleBranch(code);
                        setPickerError(null);
                      }}
                      data-testid={`bell-branch-${code}`}
                    />
                    {code}
                  </label>
                );
              })}
            </div>
          )}

          {pickerError && (
            <p className="pp-picker-error" data-testid="bell-picker-error" role="alert">
              {pickerError}
            </p>
          )}

          <button
            type="button"
            className="pp-btn pp-btn-solid pp-btn-sm"
            onClick={subscribed ? doUpdateBranches : doSubscribe}
            disabled={busy}
            data-testid="bell-branch-confirm"
          >
            {busy
              ? "Saving…"
              : subscribed
                ? "Update branches"
                : "Turn on notifications"}
          </button>
        </div>
      )}

      {needsIosInstall && (
        <div
          className="pp-bell-popover"
          data-testid="bell-ios-hint"
          role="status"
        >
          <p>
            On iPhone, notifications only work once Placement Pulse is
            added to your Home Screen — tap <b>Share → Add to Home
            Screen</b>, then open it from there and tap the bell again.
          </p>
          <button
            type="button"
            className="pp-btn-link"
            onClick={() => setNeedsIosInstall(false)}
            data-testid="bell-ios-hint-dismiss"
          >
            Got it
          </button>
        </div>
      )}

      {error && (
        <div
          className="pp-bell-popover pp-bell-popover-error"
          data-testid="bell-error"
          role="alert"
        >
          <p>{error}</p>
          <button
            type="button"
            className="pp-btn-link"
            onClick={() => setError(null)}
            data-testid="bell-error-dismiss"
          >
            Dismiss
          </button>
        </div>
      )}

      {showConfirm && (
        <div
          className="pp-bell-confirm-toast"
          data-testid="bell-confirm-toast"
          role="status"
        >
          <strong>You&rsquo;re subscribed</strong>
          <span>
            {pickedBranches.length > 0
              ? `You'll get notified for ${pickedBranches.join(", ")} and all-branches drives.`
              : "You'll get notified the moment a new all-branches drive is posted."}
          </span>
        </div>
      )}
    </div>
  );
}