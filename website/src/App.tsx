import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import { lazy, Suspense, useEffect } from "react";
import NavV2 from "./components/NavV2";
import ScrollProgress from "./components/ScrollProgress";
import CursorFollower from "./components/effects/CursorFollower";
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
      className="flex min-h-screen items-center justify-center font-mono text-sm"
      style={{ color: "var(--color-text-on-cream-muted)" }}
    >
      Loading…
    </div>
  );
}

function HomeOnlyScrollProgress() {
  // Indicator only on the marketing home page. Console + docs have their own
  // chrome (sidebars, sticky toolbars) where a top hairline would clash.
  const { pathname } = useLocation();
  if (pathname !== "/") return null;
  return <ScrollProgress />;
}

export default function App() {
  return (
    <>
      <ScrollReset />
      <HomeOnlyScrollProgress />
      <CursorFollower />
      <NavV2 />
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-[100] focus:rounded-md focus:px-3 focus:py-2 focus:font-mono focus:text-xs"
        style={{
          backgroundColor: "var(--color-accent)",
          color: "var(--color-cream)",
        }}
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
          {/* Anything else (including legacy /pricing) goes home. */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </>
  );
}
