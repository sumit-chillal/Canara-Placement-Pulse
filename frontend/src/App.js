import { useEffect, useState } from "react";
import { BrowserRouter, Routes, Route, useNavigate } from "react-router-dom";
import Home from "@/pages/Home";
import EntryDetail from "@/pages/EntryDetail";
import ErrorBoundary from "@/components/ErrorBoundary";
import "@/App.css";

/**
 * Bridges SW → App navigation for browsers that don't support
 * WindowClient.navigate() (e.g. some iOS builds). The SW posts a
 * {type:'PP_NAVIGATE', path} message; we react by pushing the route.
 */
function SWNavigationBridge() {
  const navigate = useNavigate();
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    const handler = (event) => {
      const msg = event.data;
      if (msg && msg.type === "PP_NAVIGATE" && typeof msg.path === "string") {
        navigate(msg.path);
      }
    };
    navigator.serviceWorker.addEventListener("message", handler);
    return () =>
      navigator.serviceWorker.removeEventListener("message", handler);
  }, [navigate]);
  return null;
}

export default function App() {
  return (
    <BrowserRouter>
      <SWNavigationBridge />
      <ErrorBoundary>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/entries/:slno" element={<EntryDetail />} />
        </Routes>
      </ErrorBoundary>
    </BrowserRouter>
  );
}