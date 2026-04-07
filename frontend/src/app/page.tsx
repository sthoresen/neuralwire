"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import SectionHeader from "@/components/SectionHeader";
import MonthlyNewsFlow from "@/components/MonthlyNewsFlow";
import FocalPoints from "@/components/FocalPoints";
import NewsSection, { Article } from "@/components/NewsSection";
import EventTimeline, { TickerEvent } from "@/components/EventTimeline";

// PriceChart uses lightweight-charts which requires the DOM — load client-side only
const PriceChart = dynamic(() => import("@/components/PriceChart"), {
  ssr: false,
});

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

interface HeaderData {
  ticker: string;
  long_name: string;
  eyebrow: string;
  description: string;
  accent: string;
  accent_light: string;
  accent_dim: string;
  accent_glow: string;
}

interface Artefact {
  content: string | null;
  generated_at: string | null;
  model_name: string | null;
}

function getStoredTicker(): string {
  if (typeof window === "undefined") return "NVDA";
  return localStorage.getItem("sn-ticker") ?? "NVDA";
}

export default function ScreenerPage() {
  const [ticker, setTicker] = useState<string>("NVDA");
  const [header, setHeader] = useState<HeaderData | null>(null);
  const [prices, setPrices] = useState<{ date: string; close: number }[]>([]);
  const [intraday, setIntraday] = useState<{ timestamp: string; close: number }[]>([]);
  const [mnf, setMnf] = useState<Artefact | null>(null);
  const [focalPoints, setFocalPoints] = useState<Artefact | null>(null);
  const [articles, setArticles] = useState<Article[]>([]);
  const [events, setEvents] = useState<TickerEvent[]>([]);
  const [loading, setLoading] = useState(true);

  // Sync ticker from localStorage + sidebar events
  useEffect(() => {
    setTicker(getStoredTicker());
    function onTickerChange(e: Event) {
      setTicker((e as CustomEvent<string>).detail);
    }
    window.addEventListener("ticker-change", onTickerChange);
    return () => window.removeEventListener("ticker-change", onTickerChange);
  }, []);

  // Fetch all data when ticker changes
  useEffect(() => {
    if (!ticker) return;
    setLoading(true);

    Promise.all([
      fetch(`${API}/ticker/${ticker}/header`).then((r) => r.json()),
      fetch(`${API}/ticker/${ticker}/prices`).then((r) => r.json()),
      fetch(`${API}/ticker/${ticker}/intraday`).then((r) => r.json()),
      fetch(`${API}/ticker/${ticker}/monthly-news-flow`).then((r) => r.json()),
      fetch(`${API}/ticker/${ticker}/focal-points`).then((r) => r.json()),
      fetch(`${API}/ticker/${ticker}/articles`).then((r) => r.json()),
      fetch(`${API}/ticker/${ticker}/events`).then((r) => r.json()),
    ])
      .then(([h, p, intra, mnfData, fpData, arts, evts]) => {
        setHeader(h);
        setPrices(p.prices ?? []);
        setIntraday(intra.intraday ?? []);
        setMnf(mnfData);
        setFocalPoints(fpData);
        setArticles(arts.articles ?? []);
        setEvents(evts.events ?? []);
        // Apply accent CSS variables
        document.documentElement.style.setProperty("--sn-accent", h.accent);
        document.documentElement.style.setProperty("--sn-accent-dim", h.accent_dim);
        document.documentElement.style.setProperty("--sn-accent-glow", h.accent_glow);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [ticker]);

  // Derived price values
  const latestPrice = prices.length ? prices[prices.length - 1].close : null;
  const prevPrice = prices.length > 1 ? prices[prices.length - 2].close : latestPrice;
  const priceChange = latestPrice != null && prevPrice != null ? latestPrice - prevPrice : null;
  const pricePct = priceChange != null && prevPrice ? (priceChange / prevPrice) * 100 : null;
  const up = (priceChange ?? 0) >= 0;
  const accent = header?.accent ?? "#76b900";

  return (
    <>
      {loading && (
        <p className="text-[var(--sn-text-tertiary)] text-sm mt-4">Loading…</p>
      )}

      {!loading && header && (
        <>
          {/* ── Hero ─────────────────────────────────────────────────── */}
          <div className="relative mb-12">
            <div
              className="absolute pointer-events-none -z-10"
              style={{
                top: -100,
                left: "40%",
                transform: "translateX(-50%)",
                width: 500,
                height: 250,
                background:
                  "radial-gradient(ellipse, var(--sn-accent-glow) 0%, transparent 70%)",
              }}
            />
            <p className="font-mono text-[11px] uppercase tracking-[0.12em] text-[var(--sn-text-tertiary)] mb-3">
              {header.eyebrow}
            </p>
            <div className="flex items-baseline gap-4 flex-wrap mb-2">
              <h1 className="font-serif text-[48px] font-bold tracking-[-0.02em] leading-[1.1] text-[var(--sn-text)]">
                {header.ticker}
              </h1>
              <span className="text-[20px] font-light text-[var(--sn-text-tertiary)]">
                {header.long_name}
              </span>
            </div>
            {latestPrice != null && (
              <div className="flex items-baseline gap-3.5 mt-5">
                <span className="font-mono text-[32px] font-medium text-[var(--sn-text)]">
                  ${latestPrice.toFixed(2)}
                </span>
                {priceChange != null && pricePct != null && (
                  <span
                    className="font-mono text-[14px] font-medium px-[10px] py-1 rounded-[var(--sn-radius-xs)]"
                    style={{
                      color: up ? "var(--sn-accent)" : "var(--sn-red)",
                      background: up ? "var(--sn-accent-dim)" : "var(--sn-red-dim)",
                    }}
                  >
                    {up ? "+" : ""}{priceChange.toFixed(2)} ({up ? "+" : ""}{pricePct.toFixed(2)}%)
                  </span>
                )}
              </div>
            )}
            <p className="max-w-[680px] text-[14px] leading-[1.7] font-light mt-4 text-[var(--sn-text-secondary)]">
              {header.description}
            </p>
          </div>

          {/* ── Price Chart ───────────────────────────────────────────── */}
          <SectionHeader label="Price Chart" mt="0" />
          <PriceChart prices={prices} intraday={intraday} accent={accent} />

          {/* ── Monthly News Flow + What's Moving ───────────────────── */}
          <MonthlyNewsFlow artefact={mnf} />

          {/* ── Focal Points ──────────────────────────────────────────── */}
          <SectionHeader label="Focal Points" />
          <FocalPoints artefact={focalPoints} />

          {/* ── Latest News ───────────────────────────────────────────── */}
          <SectionHeader label="Latest News" />
          <NewsSection articles={articles} accent={accent} />

          {/* ── Event Timeline ────────────────────────────────────────── */}
          <SectionHeader label="Event Timeline" />
          <EventTimeline events={events} accent={accent} />
        </>
      )}
    </>
  );
}
