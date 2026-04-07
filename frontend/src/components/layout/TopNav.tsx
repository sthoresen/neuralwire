"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { Moon, Sun } from "lucide-react";

const PAGE_NAMES: Record<string, string> = {
  "/":         "Screener",
  "/coverage": "Coverage",
};

export default function TopNav() {
  const pathname = usePathname();
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [ticker, setTicker] = useState<string>("");

  // Apply stored theme on mount (runs once, no remount on navigation)
  useEffect(() => {
    const stored = (localStorage.getItem("sn-theme") as "dark" | "light") ?? "dark";
    setTheme(stored);
    document.documentElement.setAttribute("data-theme", stored);
  }, []);

  // Track active ticker from localStorage + sidebar events
  useEffect(() => {
    setTicker(localStorage.getItem("sn-ticker") ?? "NVDA");
    function onTickerChange(e: Event) {
      setTicker((e as CustomEvent<string>).detail);
    }
    window.addEventListener("ticker-change", onTickerChange);
    return () => window.removeEventListener("ticker-change", onTickerChange);
  }, []);

  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    localStorage.setItem("sn-theme", next);
    document.documentElement.setAttribute("data-theme", next);
  }

  const pageName = PAGE_NAMES[pathname] ?? "";

  return (
    <nav
      className="fixed top-0 left-0 right-0 z-[99999] h-[52px] px-8 flex items-center gap-4
                 backdrop-blur-xl border-b transition-[background,border-color] duration-300"
      style={{
        background:        theme === "dark" ? "rgba(10,10,11,0.85)" : "rgba(245,245,243,0.9)",
        borderBottomColor: theme === "dark" ? "#1e1e21" : "#e0e0dc",
      }}
    >
      {/* Logo */}
      <span className="font-serif text-[15px] font-bold tracking-tight text-[var(--sn-text)]">
        Pulse
      </span>

      {/* Divider */}
      <div className="w-px h-5 bg-[var(--sn-border)]" />

      {/* Page name */}
      {pageName && (
        <span className="text-[13px] font-medium text-[var(--sn-text-secondary)]">
          {pageName}
        </span>
      )}

      {/* Ticker badge — shown on Screener only */}
      {pathname === "/" && ticker && (
        <span
          className="font-mono text-[12px] font-semibold px-2 py-[3px] rounded-[4px] tracking-[0.05em]"
          style={{ color: "var(--sn-accent)", background: "var(--sn-accent-dim)" }}
        >
          {ticker}
        </span>
      )}

      {/* Theme toggle */}
      <button
        onClick={toggleTheme}
        className="ml-auto flex items-center gap-2 cursor-pointer select-none"
        aria-label="Toggle theme"
      >
        <span className="font-mono text-[11px] text-[var(--sn-text-tertiary)] tracking-[0.04em] hidden sm:block">
          Darkmode:{" "}
          <span style={{ color: "var(--sn-accent)" }}>
            {theme === "dark" ? "on" : "off"}
          </span>
        </span>
        <div
          className="relative w-10 h-[22px] rounded-full transition-colors duration-300"
          style={{ background: theme === "dark" ? "#27272a" : "#d1d1cd" }}
        >
          <div
            className="absolute top-[3px] left-[3px] w-4 h-4 rounded-full flex items-center justify-center transition-all duration-300"
            style={{
              background: theme === "dark" ? "#a1a1aa" : "#ffffff",
              transform:  theme === "dark" ? "translateX(18px)" : "translateX(0)",
            }}
          >
            {theme === "dark" ? (
              <Moon size={9} className="text-[#71717a]" />
            ) : (
              <Sun size={9} className="text-amber-600" />
            )}
          </div>
        </div>
      </button>
    </nav>
  );
}
