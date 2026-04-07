"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();

  const [tickers, setTickers] = useState<string[]>(["NVDA"]);
  const [selected, setSelected] = useState<string>("NVDA");

  // Load ticker list
  useEffect(() => {
    fetch(`${API}/tickers`)
      .then((r) => r.json())
      .then((d) => {
        if (d.tickers?.length) setTickers(d.tickers);
      })
      .catch(() => {});
  }, []);

  // Persist selected ticker in localStorage so pages can read it
  useEffect(() => {
    const stored = localStorage.getItem("sn-ticker");
    if (stored && stored !== selected) setSelected(stored);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleTickerChange(e: React.ChangeEvent<HTMLSelectElement>) {
    const t = e.target.value;
    setSelected(t);
    localStorage.setItem("sn-ticker", t);
    // Trigger a custom event so page components can react without a full navigation
    window.dispatchEvent(new CustomEvent("ticker-change", { detail: t }));
  }

  const navLink = (href: string, label: string, icon: string) => {
    const active = pathname === href;
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
      {navLink("/", "Screener", "📈")}
      {navLink("/coverage", "Coverage", "📰")}

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
        value={selected}
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
