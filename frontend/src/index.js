import React from "react";
import ReactDOM from "react-dom/client";
import "@/index.css";
import App from "@/App";

// Register the SW AFTER first paint so the initial data fetch + render
// pass isn't racing with SW install in memory-constrained environments.
// `?nosw=1` disables registration entirely — useful for headless test
// runners that can't spare the extra memory (see Memory.md).
if (
  "serviceWorker" in navigator &&
  !new URLSearchParams(window.location.search).has("nosw")
) {
  const register = () =>
    navigator.serviceWorker
      .register("/firebase-messaging-sw.js")
      .catch((err) => console.warn("SW registration failed:", err));
  if ("requestIdleCallback" in window) {
    window.requestIdleCallback(register, { timeout: 5000 });
  } else {
    window.addEventListener("load", () => setTimeout(register, 2500));
  }
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
