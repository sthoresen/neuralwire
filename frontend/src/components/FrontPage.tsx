"use client";

import { Fragment, useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, ExternalLink, Heart } from "lucide-react";
import SectionHeader from "@/components/SectionHeader";
import { MediaStrip, PostText, QuotedPost, XPostBase, fmtCount } from "@/components/XPost";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

type Section = "ai" | "macro";
type Period = "day" | "week";

interface Digest {
  edition_at: string; // when it was written; the briefing covers the 24 hours or 7 days before
  edition_date: string;
  // Daily: a lead and "Also" lines. Weekly: short stories. Older briefings: paragraphs only.
  content: {
    title: string;
    paragraphs?: string[];
    also?: string[];
    stories?: { heading: string; text: string }[];
    post_ids: string[];
  };
  model_name: string | null;
}
type FrontPost = XPostBase & { author_name: string | null; group_name: string };

interface FrontPage {
  editions: string[]; // when daily briefings were written, newest first
  digests: Record<Section, Record<Period, Digest | null>>;
  showcase: Record<Period, string[]>;
  posts: Record<string, FrontPost>;
}

// "AI news", not "AI": the section is about the AI sector, not written by one.
const SECTION_TITLES: Record<Section, string> = { ai: "AI news", macro: "Market backdrop" };
const CITATION = /\[\[(\d+)\]\]/g;

// Dates on this page are the reader's own: briefings are grouped by their local day.
/** "2026-10-09": the reader's local date of a moment. */
const localDate = (iso: string) => new Date(iso).toLocaleDateString("en-CA");

/** The last moment of a local date, as an ISO timestamp for the API's `until`. */
function endOfLocalDay(date: string) {
  const [y, m, d] = date.split("-").map(Number);
  return new Date(y, m - 1, d, 23, 59, 59, 999).toISOString();
}

/** "Friday, October 9 at 4:18 PM GMT+2": when a briefing was written, in the reader's time zone. */
const fmtEdition = (iso: string) =>
  new Date(iso).toLocaleString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });

// A run of citation markers, with any space before it: "October [[1]][[2]]."
const CITATION_RUN = /\s*((?:\[\[\d+\]\])+)/g;

/** Text with each run of [[post_id]] markers drawn as one small group of numbered links. */
function Cited({ text, numbers, posts }: { text: string; numbers: Map<string, number>; posts: FrontPage["posts"] }) {
  const parts = text.split(CITATION_RUN); // even indexes: prose, odd: a run of markers
  return (
    <>
      {parts.map((part, i) => {
        if (i % 2 === 0) return <Fragment key={i}>{part}</Fragment>;
        const cited = [...part.matchAll(CITATION)].map((m) => m[1]).filter((id) => posts[id] && numbers.has(id));
        if (!cited.length) return null;
        return (
          <sup key={i} className="font-mono text-[9.5px] leading-none ml-[1px]">
            {cited.map((id, j) => (
              <Fragment key={id}>
                {j > 0 && <span className="text-[var(--sn-text-tertiary)]">,</span>}
                <a
                  href={posts[id].url}
                  target="_blank"
                  rel="noopener noreferrer"
                  title={`${posts[id].author_name ?? "@" + posts[id].author_handle}: ${(posts[id].text ?? "").slice(0, 140)}`}
                  className="text-[var(--sn-text-tertiary)] hover:text-[var(--sn-accent)] transition-colors"
                >
                  {numbers.get(id)}
                </a>
              </Fragment>
            ))}
          </sup>
        );
      })}
    </>
  );
}

const PROSE = "text-[14.5px] leading-[1.75] font-light text-[var(--sn-text-secondary)]";

/** One muted line naming each cited account once, linking to its first cited post. */
function Sources({ ids, posts }: { ids: string[]; posts: FrontPage["posts"] }) {
  const firstByAuthor = new Map<string, FrontPost>();
  ids.forEach((id) => {
    const p = posts[id];
    if (p && !firstByAuthor.has(p.author_handle)) firstByAuthor.set(p.author_handle, p);
  });
  return (
    <p className="mt-5 pt-3 border-t text-[12px] leading-[1.7] text-[var(--sn-text-tertiary)]" style={{ borderColor: "var(--sn-border-subtle)" }}>
      <span className="text-[var(--sn-text-secondary)]">Sources:</span>{" "}
      {[...firstByAuthor.values()].map((p, i) => (
        <Fragment key={p.author_handle}>
          {i > 0 && ", "}
          <a
            href={p.url}
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-[var(--sn-text)] transition-colors"
          >
            {p.author_name || `@${p.author_handle}`}
          </a>
        </Fragment>
      ))}
    </p>
  );
}

function Briefing({ digest, posts, empty }: { digest: Digest | null; posts: FrontPage["posts"]; empty: string }) {
  if (!digest) return <p className="text-[13px] text-[var(--sn-text-tertiary)]">{empty}</p>;
  const { title, paragraphs = [], also = [], stories = [] } = digest.content;
  // Number sources in order of first citation, so the numbers read 1, 2, 3 down the text.
  const numbers = new Map<string, number>();
  [...paragraphs, ...stories.map((st) => st.text), ...also].forEach((text) => {
    for (const m of text.matchAll(CITATION)) {
      if (posts[m[1]] && !numbers.has(m[1])) numbers.set(m[1], numbers.size + 1);
    }
  });
  const cited = (text: string) => <Cited text={text} numbers={numbers} posts={posts} />;
  return (
    <>
      <h3 className="font-serif text-[20px] sm:text-[22px] font-medium leading-[1.3] text-[var(--sn-text)] mb-4">{title}</h3>
      <div className="space-y-4">
        {paragraphs.map((para, i) => (
          <p key={i} className={PROSE}>{cited(para)}</p>
        ))}
        {stories.map((st, i) => (
          <div key={i}>
            <h4 className="text-[14.5px] font-semibold text-[var(--sn-text)] mb-1">{st.heading}</h4>
            <p className={PROSE}>{cited(st.text)}</p>
          </div>
        ))}
        {also.length > 0 && (
          <div className="pt-1">
            <div className="flex items-center gap-3 mb-2.5">
              <span className={LABEL}>Also</span>
              <span className="flex-1 h-px" style={{ background: "var(--sn-border-subtle)" }} />
            </div>
            <ul className="space-y-1.5">
              {also.map((line, i) => (
                <li key={i} className="flex gap-2.5 text-[14px] leading-[1.65] font-light text-[var(--sn-text-secondary)]">
                  <span className="mt-[10px] h-1 w-1 shrink-0 rounded-full" style={{ background: "var(--sn-text-tertiary)" }} />
                  <span>{cited(line)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
      {numbers.size > 0 && <Sources ids={[...numbers.keys()]} posts={posts} />}
    </>
  );
}

// Small labels in the sans face at a readable size; the mono kicker style is too faint for them.
const LABEL = "text-[11.5px] font-semibold uppercase tracking-[0.1em] text-[var(--sn-text-secondary)]";

function Panel({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section
      className="rounded-[var(--sn-radius)] border p-[18px] sm:p-[24px_26px] h-full"
      style={{ background: "var(--sn-surface)", borderColor: "var(--sn-border-subtle)" }}
    >
      <p className={`${LABEL} flex items-center gap-2 mb-3`}>
        <span className="h-1.5 w-1.5 rounded-full" style={{ background: "var(--sn-accent)" }} />
        {label}
      </p>
      {children}
    </section>
  );
}

// Posts sit on a lighter surface than the briefing panels, so they read as quoted material.
function TopPost({ post }: { post: FrontPost }) {
  return (
    <div
      className="rounded-[var(--sn-radius)] border p-[16px_18px] flex flex-col transition-colors hover:border-[var(--sn-text-tertiary)]"
      style={{ background: "var(--sn-surface-2)", borderColor: "var(--sn-border)" }}
    >
      <div className="flex items-baseline gap-2 mb-2">
        <a
          href={`https://x.com/${post.author_handle}`}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[13px] font-semibold text-[var(--sn-text)] hover:text-[var(--sn-accent)] transition-colors truncate"
        >
          {post.author_name || `@${post.author_handle}`}
        </a>
        {post.author_name && (
          <span className="font-mono text-[11px] text-[var(--sn-text-tertiary)] truncate">@{post.author_handle}</span>
        )}
        <div className="flex-1" />
        <span className="inline-flex items-center gap-1 font-mono text-[11px] text-[var(--sn-text-tertiary)]">
          <Heart size={11} />
          {fmtCount(post.likes)}
        </span>
      </div>
      <p className="text-[13.5px] text-[var(--sn-text-secondary)] leading-[1.6] whitespace-pre-line break-words line-clamp-5">
        <PostText text={post.text} links={post.links} media={post.media} />
      </p>
      <MediaStrip media={post.media.slice(0, 1)} size="md" />
      {post.quoted && <QuotedPost quoted={post.quoted} compact />}
      <div className="flex-1" />
      <a
        href={post.url}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1 mt-3 font-mono text-[11px] text-[var(--sn-text-tertiary)] hover:text-[var(--sn-accent)] transition-colors"
      >
        Open on X <ExternalLink size={11} />
      </a>
    </div>
  );
}

export default function FrontPage() {
  const [day, setDay] = useState<string | null>(null); // a local date; null = the latest
  const [period, setPeriod] = useState<Period>("day");
  const [data, setData] = useState<FrontPage | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);
    const query = day ? `?until=${encodeURIComponent(endOfLocalDay(day))}` : "";
    fetch(`${API}/front-page${query}`, { signal: ctrl.signal })
      .then((r) => r.json())
      .then((d: FrontPage) => setData(d))
      .catch((e) => { if (e.name !== "AbortError") setData(null); })
      .finally(() => { if (!ctrl.signal.aborted) setLoading(false); });
    return () => ctrl.abort();
  }, [day]);

  if (!loading && (!data || !data.editions?.length)) {
    return (
      <p className="text-[13px] text-[var(--sn-text-tertiary)]">
        No briefings yet. Run <code>python run_x_refresh.py</code> first.
      </p>
    );
  }

  const dates = [...new Set((data?.editions ?? []).map(localDate))];
  const latestDaily = data?.digests.ai?.day ?? data?.digests.macro?.day;
  const current = latestDaily ? localDate(latestDaily.edition_at) : (dates[0] ?? null);
  const idx = current ? dates.indexOf(current) : -1;
  const showcase = (data?.showcase[period] ?? []).map((id) => data?.posts[id]).filter(Boolean) as FrontPost[];
  const shown = data?.digests.ai[period] ?? data?.digests.macro[period];
  const asOf = shown
    ? `${period === "day" ? "The last 24 hours" : "The last 7 days"}, as of ${fmtEdition(shown.edition_at)}`
    : null;

  const tab = (p: Period, label: string) => (
    <button
      onClick={() => setPeriod(p)}
      className={`px-4 py-1.5 rounded-[var(--sn-radius-xs)] text-[13px] font-medium transition-colors ${
        period === p
          ? "bg-[var(--sn-surface-3)] text-[var(--sn-text)]"
          : "text-[var(--sn-text-secondary)] hover:text-[var(--sn-text)]"
      }`}
    >
      {label}
    </button>
  );

  return (
    <>
      <div className="mb-6">
        <div className="flex items-center justify-between gap-4 flex-wrap mb-4">
          <div
            className="inline-flex p-1 rounded-[var(--sn-radius-sm)] border"
            style={{ background: "var(--sn-surface)", borderColor: "var(--sn-border-subtle)" }}
          >
            {tab("day", "Daily")}
            {tab("week", "Weekly")}
          </div>

          {/* Editions are newest first, so the previous day is idx + 1. */}
          <div className="flex items-center gap-2">
            {loading && <span className="font-mono text-[11px] text-[var(--sn-text-tertiary)]">Loading…</span>}
            <button
              onClick={() => setDay(dates[idx + 1])}
              disabled={idx < 0 || idx >= dates.length - 1}
              className="p-1.5 rounded-[var(--sn-radius-xs)] text-[var(--sn-text-secondary)] hover:bg-[var(--sn-surface-2)] disabled:opacity-30"
              aria-label="Previous day"
            >
              <ChevronLeft size={16} />
            </button>
            <select
              value={current ?? ""}
              onChange={(e) => setDay(e.target.value)}
              className="font-mono text-[13px] px-2 py-1 rounded-[var(--sn-radius-xs)] border"
              style={{ background: "var(--sn-surface)", borderColor: "var(--sn-border)", color: "var(--sn-text)" }}
            >
              {dates.map((d) => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
            <button
              onClick={() => setDay(dates[idx - 1])}
              disabled={idx <= 0}
              className="p-1.5 rounded-[var(--sn-radius-xs)] text-[var(--sn-text-secondary)] hover:bg-[var(--sn-surface-2)] disabled:opacity-30"
              aria-label="Next day"
            >
              <ChevronRight size={16} />
            </button>
          </div>
        </div>

        <h1 className="font-serif text-[30px] sm:text-[40px] font-bold tracking-[-0.02em] leading-[1.1] text-[var(--sn-text)]">
          Latest from X
        </h1>
        {asOf && <p className="mt-2 text-[14px] text-[var(--sn-text-secondary)]">{asOf}</p>}
      </div>

      {data && data.editions.length > 0 && (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {(["ai", "macro"] as Section[]).map((s) => (
              <Panel key={s} label={SECTION_TITLES[s]}>
                <Briefing
                  digest={data.digests[s][period]}
                  posts={data.posts}
                  empty={`No ${period === "day" ? "daily" : "weekly"} briefing for this date.`}
                />
              </Panel>
            ))}
          </div>

          {showcase.length > 0 && (
            <>
              <SectionHeader label={period === "day" ? "Top posts of the day" : "Top posts of the week"} />
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {showcase.map((p) => (
                  <TopPost key={p.post_id} post={p} />
                ))}
              </div>
            </>
          )}

          <p className="mt-8 text-[12px] text-[var(--sn-text-tertiary)]">
            Summaries written by an LLM from selected posts on X.
          </p>
        </>
      )}
    </>
  );
}
