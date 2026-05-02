import { Navigate } from "react-router-dom";

// Bare /docs lands on the quickstart — most people want to install first.
export default function DocsIndex() {
  return <Navigate to="/docs/quickstart" replace />;
}
