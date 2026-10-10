"use client";

import { useEffect, useMemo, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  Eye,
  Heart,
  Repeat2,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";
import SectionHeader from "@/components/SectionHeader";
import { MediaStrip, PostText, QuotedPost, XPostBase, fmtCount as fmt } from "@/components/XPost";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

type Group = "ai" | "macro" | "wire";
const GROUPS: { key: Group; label: string }[] = [
  { key: "ai", label: "AI" },
  { key: "macro", label: "Macro" },
  { key: "wire", label: "Wire" },
];
type Verdict = "keep" | "skip" | null;

type Scores = Record<string, number>;

interface Post extends XPostBase {
  scores: Scores | null;
  rank: number | null;
  judgment: Verdict;
}

interface Run {
  source: "apify" | "x_counts" | "jev";
  label: string;
  items: number;
  est_cost_usd: number;
  error: string | null;
}

interface DayData {
  date: string;
  qset: string;
  judging: boolean;
  groups: Record<Group, Post[]>;
  runs: Run[];
}

// Grayscale by value, like the coverage page's score bars.
function scoreColor(v: number): string {
  if (v >= 0.7) return "var(--sn-text)";
  if (v >= 0.4) return "var(--sn-text-secondary)";
  return "var(--sn-text-tertiary)";
}

// Worth questions first, then the narrow signals; any other question after those.
const SCORE_ORDER = ["ai_daily", "macro_daily", "news", "substance", "promo", "ai", "macro"];

function ScoreChips({ scores, rank }: { scores: Scores; rank: number | null }) {
  const keys = Object.keys(scores);
  const order = [...SCORE_ORDER.filter((k) => k in scores), ...keys.filter((k) => !SCORE_ORDER.includes(k))];
  return (
    <div className="flex items-center gap-3 flex-wrap font-mono text-[10.5px]">
      {rank != null && (
        <span
          className="px-1.5 py-0.5 rounded-[4px] font-medium"
          style={{ background: "var(--sn-accent-dim)", color: scoreColor(rank) }}
        >
          rank {rank.toFixed(2)}
        </span>
      )}
      {order.map((k) => (
        <span key={k} style={{ color: scoreColor(k === "promo" ? 1 - scores[k] : scores[k]) }}>
          {k} {scores[k].toFixed(2)}
        </span>
      ))}
    </div>
  );
}

function Judge({ verdict, onJudge }: { verdict: Verdict; onJudge: (v: Verdict) => void }) {
  const btn = (v: "keep" | "skip", icon: React.ReactNode, label: string) => (
    <button
      onClick={() => onJudge(verdict === v ? null : v)}
      title={label}
      className="inline-flex items-center gap-1 px-2 py-1 rounded-[var(--sn-radius-xs)] border font-mono text-[11px] transition-colors"
      style={{
        borderColor: verdict === v ? (v === "keep" ? "var(--sn-accent)" : "var(--sn-red)") : "var(--sn-border-subtle)",
        color: verdict === v ? "var(--sn-text)" : "var(--sn-text-tertiary)",
        background: verdict === v ? (v === "keep" ? "var(--sn-accent-dim)" : "var(--sn-red-dim)") : "transparent",
      }}
    >
      {icon}
      {label}
    </button>
  );
  return (
    <div className="flex gap-1.5">
      {btn("keep", <ThumbsUp size={11} />, "Keep")}
      {btn("skip", <ThumbsDown size={11} />, "Skip")}
    </div>
  );
}

function PostCard({
  post,
  judging,
  onJudge,
}: {
  post: Post;
  judging: boolean;
  onJudge: (id: string, v: Verdict) => void;
}) {
  const time = post.created_at.slice(11, 16);
  return (
    <div
      className="rounded-[var(--sn-radius)] border p-[18px_22px] mb-[10px] transition-colors hover:border-[var(--sn-border)]"
      style={{ background: "var(--sn-surface)", borderColor: "var(--sn-border-subtle)" }}
    >
      <div className="flex items-center gap-[10px] mb-[8px] flex-wrap">
        <a
          href={`https://x.com/${post.author_handle}`}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[13px] font-semibold text-[var(--sn-text)] hover:text-[var(--sn-accent)] transition-colors"
        >
          @{post.author_handle}
        </a>
        <span className="font-mono text-[11px] text-[var(--sn-text-tertiary)]">{time} UTC</span>
        <div className="flex-1" />
        <span className="inline-flex items-center gap-3 font-mono text-[11px] text-[var(--sn-text-tertiary)]">
          <span className="inline-flex items-center gap-1"><Heart size={12} />{fmt(post.likes)}</span>
          <span className="inline-flex items-center gap-1"><Repeat2 size={12} />{fmt(post.reposts)}</span>
          <span className="inline-flex items-center gap-1"><Eye size={12} />{fmt(post.views)}</span>
        </span>
      </div>

      <p className="text-[14px] text-[var(--sn-text-secondary)] leading-[1.65] whitespace-pre-line break-words">
        <PostText text={post.text} links={post.links} media={post.media} />
      </p>
      <MediaStrip media={post.media} />

      {post.quoted && <QuotedPost quoted={post.quoted} />}

      <div className="flex items-center gap-3 flex-wrap mt-3">
        {post.scores ? (
          <ScoreChips scores={post.scores} rank={post.rank} />
        ) : (
          <span className="font-mono text-[10.5px] text-[var(--sn-text-tertiary)]">not scored yet</span>
        )}
        <div className="flex-1" />
        {judging && <Judge verdict={post.judgment} onJudge={(v) => onJudge(post.post_id, v)} />}
        <a
          href={post.url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 font-mono text-[11px] text-[var(--sn-text-tertiary)] hover:text-[var(--sn-accent)] transition-colors"
        >
          Open on X <ExternalLink size={11} />
        </a>
      </div>
    </div>
  );
}

export default function FeedPage() {
  const [days, setDays] = useState<string[]>([]);
  const [day, setDay] = useState<string | null>(null);
  const [data, setData] = useState<DayData | null>(null);
  const [loading, setLoading] = useState(true);
  const [group, setGroup] = useState<Group>("ai");
  const [sort, setSort] = useState<"rank" | "likes" | "newest">("rank");
  const [minRank, setMinRank] = useState(0);
  const [author, setAuthor] = useState<string | null>(null);

  useEffect(() => {
    document.title = "X Feed (dev) · NeuralWire";
    fetch(`${API}/x/days`)
      .then((r) => r.json())
      .then((d) => {
        const list: string[] = (d.days ?? []).map((x: { date: string }) => x.date);
        setDays(list);
        setDay(list[0] ?? null);
        if (!list.length) setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!day) return;
    const ctrl = new AbortController();
    setLoading(true);
    fetch(`${API}/x/posts?date=${day}`, { signal: ctrl.signal })
      .then((r) => r.json())
      .then((d) => setData(d))
      .catch((e) => { if (e.name !== "AbortError") setData(null); })
      .finally(() => { if (!ctrl.signal.aborted) setLoading(false); });
    return () => ctrl.abort();
  }, [day]);

  useEffect(() => setAuthor(null), [group, day]);

  async function judge(postId: string, verdict: Verdict) {
    // Optimistic: update the card now, roll back if the API refuses.
    const set = (v: Verdict) =>
      setData((d) =>
        d && {
          ...d,
          groups: {
            ...d.groups,
            [group]: d.groups[group].map((p) => (p.post_id === postId ? { ...p, judgment: v } : p)),
          },
        }
      );
    const before = data?.groups[group].find((p) => p.post_id === postId)?.judgment ?? null;
    set(verdict);
    const r = await fetch(`${API}/x/judgments`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ post_id: postId, verdict }),
    }).catch(() => null);
    if (!r?.ok) set(before);
  }

  const posts = useMemo(() => data?.groups[group] ?? [], [data, group]);

  // Accounts in this tab by number of posts, so a dominating account is easy to spot.
  const authors = useMemo(() => {
    const n = new Map<string, number>();
    posts.forEach((p) => n.set(p.author_handle, (n.get(p.author_handle) ?? 0) + 1));
    return [...n.entries()].sort((a, b) => b[1] - a[1]);
  }, [posts]);

  const shown = useMemo(() => {
    const list = posts.filter(
      (p) => (!author || p.author_handle === author) && (p.rank ?? 0) >= minRank
    );
    if (sort === "rank") return list.sort((a, b) => (b.rank ?? -1) - (a.rank ?? -1));
    if (sort === "likes") return list.sort((a, b) => (b.likes ?? 0) - (a.likes ?? 0));
    return list.sort((a, b) => b.created_at.localeCompare(a.created_at));
  }, [posts, author, sort, minRank]);

  const idx = day ? days.indexOf(day) : -1;
  const fetched = data?.runs.filter((r) => r.source === "apify" && r.label.replace(/\d+$/, "") === group) ?? [];
  const counted = data?.runs.filter((r) => r.source === "x_counts" && r.label.replace(/\d+$/, "") === group) ?? [];
  const scored = posts.filter((p) => p.scores).length;
  const judged = posts.filter((p) => p.judgment).length;

  const pill = (active: boolean) =>
    `px-3 py-1.5 rounded-[var(--sn-radius-xs)] text-[12px] font-medium transition-colors ${
      active
        ? "bg-[var(--sn-surface-3)] text-[var(--sn-text)]"
        : "text-[var(--sn-text-secondary)] hover:bg-[var(--sn-surface-2)] hover:text-[var(--sn-text)]"
    }`;

  return (
    <>
      <div className="mb-8">
        <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-[var(--sn-text-secondary)] mb-3">
          Dev · raw input
        </p>
        <h1 className="font-serif text-[30px] sm:text-[36px] font-bold tracking-[-0.02em] leading-[1.1] text-[var(--sn-text)] mb-3">
          X Feed
        </h1>
        <p className="max-w-[680px] text-[14px] leading-[1.7] font-light text-[var(--sn-text-secondary)]">
          Every stored post from the curated accounts for one UTC day, with Jev&apos;s scores.
          {data?.judging && " Mark posts Keep or Skip to measure Jev against your judgment (run_x_eval.py)."}
        </p>
      </div>

      {/* Day navigation: days are newest first, so "previous day" is idx + 1. */}
      <div className="flex items-center gap-2 mb-5">
        <button
          onClick={() => setDay(days[idx + 1])}
          disabled={idx < 0 || idx >= days.length - 1}
          className="p-1.5 rounded-[var(--sn-radius-xs)] text-[var(--sn-text-secondary)] hover:bg-[var(--sn-surface-2)] disabled:opacity-30"
          aria-label="Previous day"
        >
          <ChevronLeft size={16} />
        </button>
        <select
          value={day ?? ""}
          onChange={(e) => setDay(e.target.value)}
          className="font-mono text-[13px] px-2 py-1 rounded-[var(--sn-radius-xs)] border"
          style={{ background: "var(--sn-surface)", borderColor: "var(--sn-border)", color: "var(--sn-text)" }}
        >
          {days.map((d) => (
            <option key={d} value={d}>{d}</option>
          ))}
        </select>
        <button
          onClick={() => setDay(days[idx - 1])}
          disabled={idx <= 0}
          className="p-1.5 rounded-[var(--sn-radius-xs)] text-[var(--sn-text-secondary)] hover:bg-[var(--sn-surface-2)] disabled:opacity-30"
          aria-label="Next day"
        >
          <ChevronRight size={16} />
        </button>
      </div>

      <div className="flex items-center gap-1 flex-wrap mb-3">
        {GROUPS.map((g) => (
          <button key={g.key} onClick={() => setGroup(g.key)} className={pill(group === g.key)}>
            {g.label}
            <span className="ml-1.5 font-mono text-[11px] text-[var(--sn-text-tertiary)]">
              {data?.groups[g.key]?.length ?? 0}
            </span>
          </button>
        ))}
        <div className="flex-1" />
        <button onClick={() => setSort("rank")} className={pill(sort === "rank")}>Rank</button>
        <button onClick={() => setSort("likes")} className={pill(sort === "likes")}>Most liked</button>
        <button onClick={() => setSort("newest")} className={pill(sort === "newest")}>Newest</button>
      </div>

      <div className="flex items-center gap-3 flex-wrap mb-1">
        <label className="flex items-center gap-2 font-mono text-[11px] text-[var(--sn-text-secondary)]">
          min rank
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={minRank}
            onChange={(e) => setMinRank(Number(e.target.value))}
            className="w-40 accent-[var(--sn-accent)]"
          />
          <span className="w-8 text-[var(--sn-text)]">{minRank.toFixed(2)}</span>
        </label>
        <span className="font-mono text-[11px] text-[var(--sn-text-tertiary)]">
          showing {shown.length} of {posts.length}
        </span>
      </div>

      {data && (
        <p className="font-mono text-[11px] text-[var(--sn-text-tertiary)] mb-1">
          fetched {fetched.reduce((s, r) => s + r.items, 0)}
          {counted.length > 0 && ` of ${counted.reduce((s, r) => s + r.items, 0)} counted by X`}
          {" · "}${fetched.reduce((s, r) => s + Number(r.est_cost_usd), 0).toFixed(4)}
          {" · "}scored {scored}/{posts.length} ({data.qset})
          {data.judging && ` · judged ${judged}`}
          {fetched.some((r) => r.error) && <span style={{ color: "var(--sn-red)" }}> · fetch failed</span>}
        </p>
      )}

      <SectionHeader label="Accounts" mt="24px" />
      <div className="flex flex-wrap gap-1.5 mb-2">
        {authors.map(([handle, n]) => (
          <button
            key={handle}
            onClick={() => setAuthor(author === handle ? null : handle)}
            className="px-2 py-1 rounded-[var(--sn-radius-xs)] border font-mono text-[11px] transition-colors"
            style={{
              borderColor: author === handle ? "var(--sn-accent)" : "var(--sn-border-subtle)",
              color: author === handle ? "var(--sn-text)" : "var(--sn-text-secondary)",
              background: "var(--sn-surface)",
            }}
          >
            {handle} <span className="text-[var(--sn-text-tertiary)]">{n}</span>
          </button>
        ))}
      </div>

      <SectionHeader label={author ? `@${author}` : "Posts"} mt="24px" />
      {loading ? (
        <p className="text-[13px] text-[var(--sn-text-tertiary)]">Loading…</p>
      ) : !days.length ? (
        <p className="text-[13px] text-[var(--sn-text-tertiary)]">
          No posts stored yet. Run <code>python run_x_daily.py</code> first.
        </p>
      ) : shown.length === 0 ? (
        <p className="text-[13px] text-[var(--sn-text-tertiary)]">No posts match for {day}.</p>
      ) : (
        shown.map((p) => (
          <PostCard key={p.post_id} post={p} judging={!!data?.judging} onJudge={judge} />
        ))
      )}
    </>
  );
}
