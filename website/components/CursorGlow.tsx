"use client";

import { useEffect, useRef } from "react";

export default function CursorGlow() {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const move = (e: MouseEvent) => {
      el.style.transform = `translate(${e.clientX - 200}px, ${e.clientY - 200}px)`;
    };
    window.addEventListener("mousemove", move);
    return () => window.removeEventListener("mousemove", move);
  }, []);

  return (
    <div
      ref={ref}
      className="pointer-events-none fixed top-0 left-0 z-0 w-[400px] h-[400px] rounded-full transition-transform duration-100 ease-out"
      style={{
        background:
          "radial-gradient(circle, rgba(124,58,237,0.06) 0%, rgba(124,58,237,0.02) 40%, transparent 70%)",
      }}
    />
  );
}
