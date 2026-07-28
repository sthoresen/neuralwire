"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import SectionHeader from "@/components/SectionHeader";
import { Article } from "@/components/NewsSection";
import { ChevronLeft, ChevronRight } from "lucide-react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
const PER_PAGE = 10;

// Neutral grayscale intensity — independent of the ticker theme so the same
// score always looks the same regardless of which ticker is selected.
function statColor(v: number): string {
  if (v >= 70) return "var(--sn-text)";
  if (v >= 40) return "var(--sn-text-secondary)";
  return "var(--sn-text-tertiary)";
}

function ScoreBar({ label, value }: { label: string; value: number }) {
  const color = statColor(value);
  return (
    <div className="flex items-center gap-1.5">
      <span className="font-mono text-[10px] text-[var(--sn-text-tertiary)] uppercase tracking-[0.06em]">
        {label}
      </span>
      <div className="w-12 h-1 rounded-sm overflow-hidden" style={{ background: "var(--sn-surface-3)" }}>
        <div className="h-full rounded-sm" style={{ width: `${value}%`, background: color }} />
      </div>
      <span className="font-mono text-[11px] font-medium min-w-[22px]" style={{ color }}>
        {value}
      </span>
    </div>
  );
}

function NewsItem({ article }: { article: Article }) {
  const title   = article.impact_headline || article.headline;
  const date    = (article.published_at || "").slice(0, 10);
  const summary = article.ai_summary
    ? article.ai_summary.length > 320
      ? article.ai_summary.slice(0, 320) + "…"
      : article.ai_summary
    : null;
  const imp = article.importance_score || 0;
  const rel = article.relevancy_score || 0;
  const brk = article.breaking_news_score || 0;

  return (
    <div
      className="rounded-[var(--sn-radius)] border p-[22px_24px] mb-[10px] transition-colors hover:border-[var(--sn-border)]"
      style={{ background: "var(--sn-surface)", borderColor: "var(--sn-border-subtle)" }}
    >
      <div className="text-[15px] font-semibold text-[var(--sn-text)] leading-[1.45] mb-[10px]">
        <a href={article.url || "#"} target="_blank" rel="noopener noreferrer"
          className="hover:text-[var(--sn-accent)] transition-colors">
          {title}
        </a>
      </div>
      <div className="flex items-center gap-[10px] text-[12px] text-[var(--sn-text-tertiary)] mb-[10px] flex-wrap">
        {article.provider && (
          <span className="font-medium text-[var(--sn-text-secondary)]">{article.provider}</span>
        )}
        {article.provider && date && (
          <span className="inline-block w-[3px] h-[3px] rounded-full" style={{ background: "var(--sn-border)" }} />
        )}
        {date && <span>{date}</span>}
      </div>
      {summary && (
        <p className="text-[13.5px] text-[var(--sn-text-secondary)] leading-[1.7] font-light mb-[14px]">
          {summary}
        </p>
      )}
      <div className="flex gap-[18px] flex-wrap">
        <ScoreBar label="IMP" value={imp} />
        <ScoreBar label="REL" value={rel} />
        <ScoreBar label="BRK" value={brk} />
      </div>
    </div>
  );
}

export default function CoveragePage() {
  const { ticker: raw } = useParams<{ ticker: string }>();
  const ticker = String(raw ?? "").toUpperCase();

  const [longName, setLongName] = useState("");
  const [articles, setArticles] = useState<Article[]>([]);
  const [loading, setLoading]   = useState(true);
  const [page, setPage]         = useState(0);

  // Filters
  const [minRel, setMinRel] = useState(50);
  const [minBrk, setMinBrk] = useState(0);
  const [minImp, setMinImp] = useState(0);

  // Reset pagination when ticker changes
  useEffect(() => {
    setPage(0);
  }, [ticker]);

  // Reflect the active ticker in the browser tab title
  useEffect(() => {
    if (ticker) document.title = `${ticker} Coverage · NeuralWire`;
  }, [ticker]);

  // Fetch header for accent + long name
  useEffect(() => {
    if (!ticker) return;
    const ctrl = new AbortController();
    fetch(`${API}/ticker/${ticker}/header`, { signal: ctrl.signal })
      .then((r) => r.json())
      .then((h) => {
        setLongName(h.long_name ?? ticker);
        document.documentElement.style.setProperty("--sn-accent", h.accent);
        document.documentElement.style.setProperty("--sn-accent-dim", h.accent_dim);
        document.documentElement.style.setProperty("--sn-accent-glow", h.accent_glow);
      })
      .catch(() => {});
    return () => ctrl.abort();
  }, [ticker]);

  // Fetch articles when ticker or filters change
  useEffect(() => {
    if (!ticker) return;
    const ctrl = new AbortController();
    setLoading(true);
    setPage(0);
    fetch(
      `${API}/ticker/${ticker}/coverage?min_relevance=${minRel}&min_breaking=${minBrk}&min_importance=${minImp}`,
      { signal: ctrl.signal }
    )
      .then((r) => r.json())
      .then((d) => setArticles(d.articles ?? []))
      .catch((e) => { if (e.name !== "AbortError") setArticles([]); })
      .finally(() => { if (!ctrl.signal.aborted) setLoading(false); });
    return () => ctrl.abort();
  }, [ticker, minRel, minBrk, minImp]);

  const total   = articles.length;
  const maxPage = Math.max(0, Math.ceil(total / PER_PAGE) - 1);
  const safe    = Math.min(page, maxPage);
  const slice   = articles.slice(safe * PER_PAGE, safe * PER_PAGE + PER_PAGE);
  const start   = total ? safe * PER_PAGE + 1 : 0;
  const end     = Math.min(safe * PER_PAGE + PER_PAGE, total);

  function Slider({
    label, value, onChange,
  }: { label: string; value: number; onChange: (v: number) => void }) {
    return (
      <div className="flex items-center gap-3">
        <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--sn-text-tertiary)] whitespace-nowrap w-28">
          {label}
        </span>
        <input
          type="range" min={0} max={100} value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className="flex-1 max-w-[160px] accent-[var(--sn-accent)]"
        />
        <span className="font-mono text-[11px] text-[var(--sn-text-secondary)] w-6 text-right">
          {value}
        </span>
      </div>
    );
  }

  return (
    <>
      {/* Page header */}
      <div className="mb-8">
        <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--sn-text-tertiary)] mb-2">
          News Analysis
        </p>
        <h1 className="font-serif text-[26px] sm:text-[32px] font-bold tracking-[-0.02em] text-[var(--sn-text)] mb-1">
          {ticker} Coverage
        </h1>
        <p className="text-[14px] text-[var(--sn-text-secondary)] font-light">
          {longName} · AI-scored article feed
        </p>
      </div>

      {/* Filters */}
      <div
        className="rounded-[var(--sn-radius)] border p-4 mb-6 flex flex-col gap-3"
        style={{ background: "var(--sn-surface)", borderColor: "var(--sn-border-subtle)" }}
      >
        <Slider label="Min Relevance" value={minRel} onChange={setMinRel} />
        <Slider label="Min Breaking"  value={minBrk} onChange={setMinBrk} />
        <Slider label="Min Importance" value={minImp} onChange={setMinImp} />
      </div>

      {loading && (
        <p className="text-[var(--sn-text-tertiary)] text-sm">Loading…</p>
      )}

      {!loading && (
        <>
          <SectionHeader label="Latest Articles" mt="0" />

          {/* Count + pagination */}
          <div className="flex items-center gap-3 mb-4">
            <span className="font-mono text-[12px] text-[var(--sn-text-tertiary)] flex-1">
              {total} articles matching filters
              {total > 0 ? ` · showing ${start}–${end}` : ""}
            </span>
            <button
              disabled={safe === 0}
              onClick={() => setPage(safe - 1)}
              className="w-8 h-8 flex items-center justify-center rounded-[var(--sn-radius-xs)] border
                         text-[var(--sn-text-secondary)] transition-colors
                         hover:bg-[var(--sn-surface-3)] hover:text-[var(--sn-text)]
                         disabled:opacity-30 disabled:cursor-not-allowed"
              style={{ background: "var(--sn-surface)", borderColor: "var(--sn-border)" }}
            >
              <ChevronLeft size={14} />
            </button>
            <button
              disabled={safe >= maxPage}
              onClick={() => setPage(safe + 1)}
              className="w-8 h-8 flex items-center justify-center rounded-[var(--sn-radius-xs)] border
                         text-[var(--sn-text-secondary)] transition-colors
                         hover:bg-[var(--sn-surface-3)] hover:text-[var(--sn-text)]
                         disabled:opacity-30 disabled:cursor-not-allowed"
              style={{ background: "var(--sn-surface)", borderColor: "var(--sn-border)" }}
            >
              <ChevronRight size={14} />
            </button>
          </div>

          {total === 0 ? (
            <div
              className="rounded-[var(--sn-radius)] border p-10 text-center"
              style={{ background: "var(--sn-surface)", borderColor: "var(--sn-border-subtle)" }}
            >
              <p className="text-[var(--sn-text-tertiary)] text-[14px]">
                No articles match the current filters.
              </p>
            </div>
          ) : (
            slice.map((article, i) => (
              <NewsItem key={i} article={article} />
            ))
          )}
        </>
      )}
    </>
  );
}
