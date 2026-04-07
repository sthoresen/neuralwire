"use client";

import { useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

export interface TickerEvent {
  title: string;
  event_date?: string;
  event_date_label?: string;
  description?: string;
}

const PER_PAGE = 5;

interface Props {
  events: TickerEvent[];
  accent: string;
}

export default function EventTimeline({ events, accent }: Props) {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);

  const q = search.toLowerCase();
  const filtered = q
    ? events.filter(
        (e) =>
          e.title.toLowerCase().includes(q) ||
          (e.description || "").toLowerCase().includes(q)
      )
    : events;

  const total = filtered.length;
  const maxPage = Math.max(0, Math.ceil(total / PER_PAGE) - 1);
  const safePage = Math.min(page, maxPage);
  const slice = filtered.slice(safePage * PER_PAGE, safePage * PER_PAGE + PER_PAGE);

  if (!events.length) {
    return (
      <p className="text-[var(--sn-text-secondary)] text-sm italic">
        No events yet.
      </p>
    );
  }

  const start = total ? safePage * PER_PAGE + 1 : 0;
  const end = Math.min(safePage * PER_PAGE + PER_PAGE, total);

  return (
    <div>
      {/* Search */}
      <input
        type="text"
        value={search}
        onChange={(e) => {
          setSearch(e.target.value);
          setPage(0);
        }}
        placeholder="earnings, acquisition, GPU …"
        className="w-full max-w-sm px-3 py-2 rounded-[var(--sn-radius-xs)] border text-[13px]
                   outline-none transition-colors mb-5"
        style={{
          background: "var(--sn-surface-2)",
          borderColor: "var(--sn-border-subtle)",
          color: "var(--sn-text)",
        }}
      />

      {/* Pagination controls */}
      {total > 0 && (
        <div className="flex items-center gap-3 mb-4">
          <span className="font-mono text-[12px] text-[var(--sn-text-tertiary)] flex-1">
            {start}–{end} of {total} events
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
      )}

      {total === 0 && (
        <p className="text-[var(--sn-text-secondary)] text-sm italic">
          No events match &ldquo;{search}&rdquo;.
        </p>
      )}

      {slice.map((ev, i) => {
        const dateLabel = ev.event_date_label || ev.event_date || "Date unknown";
        return (
          <div key={i} className="flex gap-5 mb-6">
            {/* Dot */}
            <div className="flex items-start pt-1 shrink-0">
              <div
                className="w-[10px] h-[10px] rounded-full border-2 shrink-0"
                style={{
                  borderColor: accent,
                  background: "var(--sn-bg)",
                }}
              />
            </div>

            {/* Content */}
            <div
              className="rounded-[var(--sn-radius)] border p-[18px_22px] flex-1 transition-colors"
              style={{
                background: "var(--sn-surface)",
                borderColor: "var(--sn-border-subtle)",
              }}
            >
              <div className="font-mono text-[11px] text-[var(--sn-text-tertiary)] mb-1.5 tracking-[0.04em]">
                {dateLabel}
              </div>
              <div className="text-[14px] font-semibold text-[var(--sn-text)] mb-1.5 leading-[1.4]">
                {ev.title}
              </div>
              {ev.description && (
                <div className="text-[13px] text-[var(--sn-text-secondary)] leading-[1.65] font-light">
                  {ev.description}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
