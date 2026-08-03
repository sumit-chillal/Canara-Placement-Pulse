import { useEffect, useState } from "react";
import { isIosSafari, isStandalone } from "@/lib/pwa";

const DISMISS_KEY = "pp-install-dismissed";

/**
 * One prompt to rule them all:
 *   · Chrome/Edge/Android → captures `beforeinstallprompt`, shows a
 *     custom "Install app" pill.
 *   · iOS Safari → shows an explicit onboarding sheet with Share →
 *     Add to Home Screen instructions (iOS gives us NO API for this).
 *   · Already-installed / dismissed / unsupported → renders nothing.
 */
export default function InstallPrompt() {
  const [deferred, setDeferred] = useState(null);
  const [showIos, setShowIos] = useState(false);
  const [dismissed, setDismissed] = useState(
    typeof window !== "undefined" &&
      window.localStorage?.getItem(DISMISS_KEY) === "1",
  );

  useEffect(() => {
    if (isStandalone()) return; // already installed
    if (dismissed) return;

    const onPrompt = (e) => {
      e.preventDefault();
      setDeferred(e);
    };
    window.addEventListener("beforeinstallprompt", onPrompt);

    // On iOS Safari the event never fires. Fall back to instructions.
    if (isIosSafari()) setShowIos(true);

    return () => window.removeEventListener("beforeinstallprompt", onPrompt);
  }, [dismissed]);

  const dismiss = () => {
    window.localStorage?.setItem(DISMISS_KEY, "1");
    setDismissed(true);
    setDeferred(null);
    setShowIos(false);
  };

  if (dismissed || isStandalone()) return null;

  if (deferred) {
    return (
      <div className="pp-install" data-testid="install-prompt-android">
        <div className="pp-install-copy">
          <strong>Install Placement Pulse</strong>
          <span>
            Get push notifications the second a drive drops. Adds a Placement
            Pulse icon to your home screen.
          </span>
        </div>
        <div className="pp-install-actions">
          <button
            type="button"
            className="pp-btn pp-btn-solid"
            data-testid="install-btn-android"
            onClick={async () => {
              deferred.prompt();
              const { outcome } = await deferred.userChoice;
              if (outcome === "accepted") dismiss();
              else setDeferred(null);
            }}
          >
            Install app
          </button>
          <button
            type="button"
            className="pp-btn pp-btn-ghost"
            data-testid="install-dismiss-android"
            onClick={dismiss}
          >
            Not now
          </button>
        </div>
      </div>
    );
  }

  if (showIos) {
    return (
      <div className="pp-install" data-testid="install-prompt-ios">
        <div className="pp-install-copy">
          <strong>Add to Home Screen — required for iOS</strong>
          <span>
            iPhone push notifications only work if Placement Pulse is on your
            home screen (Apple&rsquo;s rule, not ours). Two taps:
          </span>
          <ol className="pp-install-steps">
            <li>
              Tap the <b>Share</b> button in Safari
              <span aria-hidden> ⎋</span>
            </li>
            <li>
              Scroll down and choose <b>Add to Home Screen</b>
            </li>
            <li>
              Open Placement Pulse from your home screen and turn on
              notifications.
            </li>
          </ol>
        </div>
        <div className="pp-install-actions">
          <button
            type="button"
            className="pp-btn pp-btn-ghost"
            data-testid="install-dismiss-ios"
            onClick={dismiss}
          >
            Got it
          </button>
        </div>
      </div>
    );
  }

  return null;
}
