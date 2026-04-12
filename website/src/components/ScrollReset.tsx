
import { useEffect } from "react";

export default function ScrollReset() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    // Kill browser scroll restoration first
    history.scrollRestoration = "manual";
    // behavior: "instant" overrides CSS scroll-behavior: smooth
    window.scrollTo({ top: 0, left: 0, behavior: "instant" });
  }, []);
  return null;
}
