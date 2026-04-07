"use client";

import { useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

export interface Article {
  impact_headline: string | null;
  headline: string;
  url: string;
  published_at: string;
  provider: string | null;
  ai_summary: string | null;
  importance_score: number;
  relevancy_score: number;
  breaking_news_score: number;
}

const PER_PAGE = 4;

function statColor(v: number, accent: string): string {
  if (v >= 70) return accent;
  if (v >= 40) return "#f59e0b";
  return "#71717a";
}

function ScoreBar({
  label,
  value,
  accent,
}: {
  label: string;
  value: number;
  accent: string;
}) {
  const color = statColor(value, accent);
  return (
    <div className="flex items-center gap-1.5">
      <span className="font-mono text-[10px] text-[var(--sn-text-tertiary)] uppercase tracking-[0.06em]">
        {label}
      </span>
      <div
        className="w-12 h-1 rounded-sm overflow-hidden"
        style={{ background: "var(--sn-surface-3)" }}
      >
        <div
          className="h-full rounded-sm"
          style={{ width: `${value}%`, background: color }}
        />
      </div>
      <span
        className="font-mono text-[11px] font-medium min-w-[22px]"
        style={{ color }}
      >
        {value}
      </span>
    </div>
  );
}

function NewsItem({ article, accent }: { article: Article; accent: string }) {
  const title = article.impact_headline || article.headline;
  const date = (article.published_at || "").slice(0, 10);
  const summary = article.ai_summary
    ? article.ai_summary.length > 300
      ? article.ai_summary.slice(0, 300) + "…"
      : article.ai_summary
    : null;
  const imp = article.importance_score || 0;
  const rel = article.relevancy_score || 0;
  const brk = article.breaking_news_score || 0;

  return (
    <div
      className="rounded-[var(--sn-radius)] border p-[22px_24px] mb-[10px] transition-colors"
      style={{
        background: "var(--sn-surface)",
        borderColor: "var(--sn-border-subtle)",
      }}
    >
      {/* Headline */}
      <div className="text-[15px] font-semibold text-[var(--sn-text)] leading-[1.45] mb-[10px]">
        <a
          href={article.url || "#"}
          target="_blank"
          rel="noopener noreferrer"
          className="hover:text-[var(--sn-accent)] transition-colors"
        >
          {title}
        </a>
      </div>

      {/* Meta */}
      <div className="flex items-center gap-[10px] text-[12px] text-[var(--sn-text-tertiary)] mb-[10px] flex-wrap">
        {article.provider && (
          <span className="font-medium text-[var(--sn-text-secondary)]">
            {article.provider}
          </span>
        )}
        {article.provider && date && (
          <span
            className="inline-block w-[3px] h-[3px] rounded-full"
            style={{ background: "var(--sn-border)" }}
          />
        )}
        {date && <span>{date}</span>}
      </div>

      {/* Summary */}
      {summary && (
        <p className="text-[13.5px] text-[var(--sn-text-secondary)] leading-[1.7] font-light mb-[14px]">
          {summary}
        </p>
      )}

      {/* Scores */}
      <div className="flex gap-[18px] flex-wrap">
        <ScoreBar label="IMP" value={imp} accent={accent} />
        <ScoreBar label="REL" value={rel} accent={accent} />
        <ScoreBar label="BRK" value={brk} accent={accent} />
      </div>
    </div>
  );
}

interface Props {
  articles: Article[];
  accent: string;
}

export default function NewsSection({ articles, accent }: Props) {
  const [page, setPage] = useState(0);
  const total = articles.length;
  const maxPage = Math.max(0, Math.ceil(total / PER_PAGE) - 1);
  const safePage = Math.min(page, maxPage);
  const slice = articles.slice(safePage * PER_PAGE, safePage * PER_PAGE + PER_PAGE);

  if (!total) {
    return (
      <p className="text-[var(--sn-text-secondary)] text-sm italic">
        No recent articles found.
      </p>
    );
  }

  const start = safePage * PER_PAGE + 1;
  const end = Math.min(safePage * PER_PAGE + PER_PAGE, total);

  return (
    <div>
      {/* Pagination controls */}
      <div className="flex items-center gap-3 mb-4">
        <span className="font-mono text-[12px] text-[var(--sn-text-tertiary)] flex-1">
          {start}–{end} of {total} articles
        </span>
        <button
          disabled={safePage === 0}
          onClick={() => setPage(safePage - 1)}
          className="w-8 h-8 flex items-center justify-center rounded-[var(--sn-radius-xs)] border
                     text-[var(--sn-text-secondary)] transition-colors
                     hover:bg-[var(--sn-surface-3)] hover:text-[var(--sn-text)]
                     disabled:opacity-30 disabled:cursor-not-allowed"
          style={{ background: "var(--sn-surface)", borderColor: "var(--sn-border)" }}
        >
          <ChevronLeft size={14} />
        </button>
        <button
          disabled={safePage >= maxPage}
          onClick={() => setPage(safePage + 1)}
          className="w-8 h-8 flex items-center justify-center rounded-[var(--sn-radius-xs)] border
                     text-[var(--sn-text-secondary)] transition-colors
                     hover:bg-[var(--sn-surface-3)] hover:text-[var(--sn-text)]
                     disabled:opacity-30 disabled:cursor-not-allowed"
          style={{ background: "var(--sn-surface)", borderColor: "var(--sn-border)" }}
        >
          <ChevronRight size={14} />
        </button>
      </div>

      {slice.map((article, i) => (
        <NewsItem key={i} article={article} accent={accent} />
      ))}
    </div>
  );
}
