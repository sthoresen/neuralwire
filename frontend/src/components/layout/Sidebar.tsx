"use client";

import Link from "next/link";
import { usePathname, useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { ticker: raw } = useParams<{ ticker?: string }>();
  const ticker = raw ? String(raw).toUpperCase() : "";

  const [tickers, setTickers] = useState<string[]>(["NVDA"]);

  // Load ticker list
  useEffect(() => {
    fetch(`${API}/tickers`)
      .then((r) => r.json())
      .then((d) => {
        if (d.tickers?.length) setTickers(d.tickers);
      })
      .catch(() => {});
  }, []);

  const isCoverage = pathname.endsWith("/coverage");
  const isAbout = pathname === "/about";

  function handleTickerChange(e: React.ChangeEvent<HTMLSelectElement>) {
    const t = e.target.value;
    // Remember the choice so the root path can redirect here on next visit
    document.cookie = `sn-ticker=${t}; path=/; max-age=31536000; samesite=lax`;
    // Navigate, preserving the current view (screener vs coverage)
    router.push(`/${t}${isCoverage ? "/coverage" : ""}`);
  }

  const navLink = (href: string, label: string, icon: string, active: boolean) => {
    return (
      <Link
        href={href}
        className={`flex items-center gap-2 px-[10px] py-[7px] rounded-[var(--sn-radius-xs)]
                    text-[13px] font-medium transition-colors duration-150 mb-0.5
                    ${active
                      ? "bg-[var(--sn-surface-3)] text-[var(--sn-text)]"
                      : "text-[var(--sn-text-secondary)] hover:bg-[var(--sn-surface-2)] hover:text-[var(--sn-text)]"
                    }`}
      >
        <span>{icon}</span>
        {label}
      </Link>
    );
  };

  const base = ticker || "NVDA";

  return (
    <aside
      className="fixed top-0 left-0 h-full z-[9999] flex flex-col border-r"
      style={{
        width: 272,
        background: "var(--sn-bg)",
        borderColor: "var(--sn-border-subtle)",
        paddingTop: 20,
        paddingBottom: 20,
        paddingLeft: 10,
        paddingRight: 10,
      }}
    >
      {/* Logo */}
      <span
        className="font-serif text-[17px] font-bold px-[6px] pb-[14px] mb-2 block border-b"
        style={{
          color: "var(--sn-text)",
          borderColor: "var(--sn-border-subtle)",
        }}
      >
        Pulse
      </span>

      {/* Navigate section */}
      <span className="font-mono text-[9px] uppercase tracking-[0.12em] text-[var(--sn-text-tertiary)] px-[6px] pt-2 pb-1 block">
        Navigate
      </span>
      {navLink(`/${base}`, "Screener", "📈", !isCoverage && !isAbout)}
      {navLink(`/${base}/coverage`, "Coverage", "📰", isCoverage)}
      {navLink("/about", "About", "ℹ️", isAbout)}

      {/* Divider */}
      <hr
        className="my-[10px]"
        style={{ borderColor: "var(--sn-border-subtle)" }}
      />

      {/* Ticker section */}
      <span className="font-mono text-[9px] uppercase tracking-[0.12em] text-[var(--sn-text-tertiary)] px-[6px] pt-1 pb-1 block">
        Ticker
      </span>
      <select
        value={ticker || ""}
        onChange={handleTickerChange}
        className="mx-1 px-2 py-1.5 rounded-[var(--sn-radius-xs)] font-mono text-[12px]
                   tracking-[0.06em] outline-none cursor-pointer
                   border transition-colors"
        style={{
          background: "var(--sn-surface-2)",
          color: "var(--sn-text)",
          borderColor: "var(--sn-border-subtle)",
        }}
      >
        {tickers.map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
      </select>
    </aside>
  );
}
