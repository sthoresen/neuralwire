import type { Metadata } from "next";
import SectionHeader from "@/components/SectionHeader";
import { Github, Mail } from "lucide-react";

export const metadata: Metadata = {
  title: "About · Pulse",
};

const STEPS: { n: string; title: string; body: string }[] = [
  {
    n: "01",
    title: "Ingest",
    body: "Financial news for the watchlist is pulled continuously throughout the trading day.",
  },
  {
    n: "02",
    title: "Store",
    body: "Every article is saved to an ever-growing database, with AI summaries generated per article and rolled up per month — so the system can tell what's genuinely new and keep each ticker's running context up to date.",
  },
  {
    n: "03",
    title: "Analyze",
    body: "An LLM pipeline scores every article for relevance, importance, and urgency, then summarizes it.",
  },
  {
    n: "04",
    title: "Detect",
    body: "Significant events — earnings, product launches, regulatory actions — are extracted automatically.",
  },
  {
    n: "05",
    title: "Brief",
    body: "A research brief per ticker is regenerated daily, or immediately when earnings drop.",
  },
];

export default function AboutPage() {
  return (
    <>
      {/* Header */}
      <div className="mb-12">
        <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-[var(--sn-text-secondary)] mb-3">
          About
        </p>
        <div className="flex items-center gap-3 mb-4 flex-wrap">
          <h1 className="font-serif text-[40px] font-bold tracking-[-0.02em] leading-[1.1] text-[var(--sn-text)]">
            NeuralWire
          </h1>
          <span
            className="font-mono text-[10px] font-bold uppercase tracking-[0.12em] px-2 py-1 rounded-[4px]"
            style={{ color: "var(--sn-amber)", background: "rgba(245,158,11,0.12)" }}
          >
            Alpha
          </span>
        </div>
        <p className="max-w-[680px] text-[15px] leading-[1.75] font-light text-[var(--sn-text-secondary)]">
          AI-powered news intelligence for a curated watchlist of stocks. NeuralWire
          continuously reads financial news, scores and summarizes every article with an
          LLM pipeline, detects significant company events, and writes a fresh research
          brief for each ticker.
        </p>
      </div>

      {/* How it works */}
      <SectionHeader label="How it works" mt="0" />
      <div className="grid grid-cols-2 gap-3">
        {STEPS.map((s) => (
          <div
            key={s.n}
            className="rounded-[var(--sn-radius)] border p-6"
            style={{ background: "var(--sn-surface)", borderColor: "var(--sn-border-subtle)" }}
          >
            <div className="flex items-baseline gap-3 mb-2">
              <span className="font-mono text-[11px] font-medium" style={{ color: "var(--sn-accent)" }}>
                {s.n}
              </span>
              <span className="font-serif text-[16px] font-medium text-[var(--sn-text)]">
                {s.title}
              </span>
            </div>
            <p className="text-[13.5px] leading-[1.7] font-light text-[var(--sn-text-secondary)]">
              {s.body}
            </p>
          </div>
        ))}
      </div>

      {/* Why a small watchlist */}
      <SectionHeader label="Why only a handful of tickers" />
      <p className="max-w-[680px] text-[14px] leading-[1.8] font-light text-[var(--sn-text-secondary)]">
        NeuralWire runs deep, per-ticker analysis rather than scanning the whole market.
        The watchlist is intentionally small and hand-picked so each name gets thorough,
        high-quality coverage — and so the AI pipeline stays focused and affordable. More
        names may be added over time.
      </p>

      {/* Disclaimer */}
      <SectionHeader label="Disclaimer" />
      <div
        className="rounded-[var(--sn-radius)] border p-6 max-w-[680px]"
        style={{ background: "var(--sn-surface)", borderColor: "var(--sn-border-subtle)" }}
      >
        <p className="text-[13.5px] leading-[1.75] font-light text-[var(--sn-text-secondary)]">
          NeuralWire is an early-stage project under active development. Data may be
          incomplete, delayed, or inaccurate, and AI-generated summaries can contain errors.
          Nothing here is financial advice — do your own research.
        </p>
      </div>

      {/* Links */}
      <SectionHeader label="Links" />
      <div className="flex gap-3 flex-wrap">
        <a
          href="https://github.com/sthoresen/neuralwire"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-2 rounded-[var(--sn-radius-xs)] border px-4 py-2.5
                     text-[13px] font-medium text-[var(--sn-text-secondary)] transition-colors
                     hover:text-[var(--sn-text)] hover:border-[var(--sn-border)]"
          style={{ background: "var(--sn-surface)", borderColor: "var(--sn-border-subtle)" }}
        >
          <Github size={15} />
          GitHub
        </a>
        <a
          href="mailto:latentdreamlabs@gmail.com"
          className="flex items-center gap-2 rounded-[var(--sn-radius-xs)] border px-4 py-2.5
                     text-[13px] font-medium text-[var(--sn-text-secondary)] transition-colors
                     hover:text-[var(--sn-text)] hover:border-[var(--sn-border)]"
          style={{ background: "var(--sn-surface)", borderColor: "var(--sn-border-subtle)" }}
        >
          <Mail size={15} />
          latentdreamlabs@gmail.com
        </a>
      </div>
    </>
  );
}
