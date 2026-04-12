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
      className="pointer-events-none fixed top-0 left-0 z-0 w-[600px] h-[600px] rounded-full transition-transform duration-75 ease-out"
      style={{
        background:
          "radial-gradient(circle, rgba(124,58,237,0.09) 0%, rgba(124,58,237,0.04) 35%, rgba(6,182,212,0.015) 60%, transparent 75%)",
      }}
    />
  );
}
