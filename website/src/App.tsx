import { Routes, Route } from "react-router-dom";
import { lazy, Suspense, useEffect } from "react";
import CursorGlow from "./components/CursorGlow";
import AnnouncementBar from "./components/AnnouncementBar";
import Home from "./pages/Home";

// Code-split everything that isn't the LCP path. Console + Docs ship in their
// own chunks so the marketing JS stays small.
const Console = lazy(() => import("./pages/Console"));
const DocsLayout = lazy(() => import("./components/DocsLayout"));
const DocsIndex = lazy(() => import("./pages/docs/DocsIndex"));
const Quickstart = lazy(() => import("./pages/docs/Quickstart"));
const McpTools = lazy(() => import("./pages/docs/McpTools"));
const WhyCrossVendor = lazy(() => import("./pages/docs/WhyCrossVendor"));

function ScrollReset() {
  useEffect(() => {
    window.scrollTo(0, 0);
  }, []);
  return null;
}

function RouteFallback() {
  return (
    <div
      role="status"
      aria-live="polite"
      className="min-h-screen flex items-center justify-center text-white/40 text-sm font-mono"
    >
      Loading…
    </div>
  );
}

export default function App() {
  return (
    <>
      <ScrollReset />
      <CursorGlow />
      <AnnouncementBar />
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[100] focus:px-3 focus:py-2 focus:rounded-md focus:bg-teal-500 focus:text-black focus:font-mono focus:text-xs"
      >
        Skip to main content
      </a>
      <Suspense fallback={<RouteFallback />}>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/console" element={<Console />} />
          <Route path="/docs" element={<DocsLayout />}>
            <Route index element={<DocsIndex />} />
            <Route path="quickstart" element={<Quickstart />} />
            <Route path="mcp-tools" element={<McpTools />} />
            <Route path="why-cross-vendor" element={<WhyCrossVendor />} />
          </Route>
        </Routes>
      </Suspense>
    </>
  );
}
