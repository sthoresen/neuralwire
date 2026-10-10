import { Play } from "lucide-react";

// Shapes of a stored X post as the API returns it (x_posts rows).
export interface XMedia {
  type: "photo" | "video" | "animated_gif";
  image: string;
  url: string;
  tco: string;
}
export interface XLink {
  tco: string;
  url: string;
  display: string;
}
export interface XQuoted {
  author: string;
  text: string | null;
  url: string;
  media: XMedia[];
  links: XLink[];
}
export interface XPostBase {
  post_id: string;
  author_handle: string;
  created_at: string;
  text: string | null;
  url: string;
  likes: number | null;
  reposts: number | null;
  views: number | null;
  media: XMedia[];
  links: XLink[];
  quoted: XQuoted | null;
}

const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
export const fmtCount = (n: number | null) => (n == null ? "–" : compact.format(n));

// X escapes these three in post text.
const decode = (t: string) => t.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">");

/** Post text with t.co links shown as their real destination and media links removed. */
export function PostText({ text, links, media }: { text: string | null; links: XLink[]; media: XMedia[] }) {
  if (!text) return null;
  const byTco = new Map(links.map((l) => [l.tco, l]));
  const mediaTco = new Set(media.map((m) => m.tco));
  const parts = decode(text).split(/(https:\/\/t\.co\/\w+)/g);
  return (
    <>
      {parts.map((part, i) => {
        if (mediaTco.has(part)) return null;
        const link = byTco.get(part);
        if (!link) return <span key={i}>{part}</span>;
        return (
          <a
            key={i}
            href={link.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[var(--sn-accent)] hover:underline"
          >
            {link.display}
          </a>
        );
      })}
    </>
  );
}

const MEDIA_HEIGHT = { sm: "h-[72px] w-auto", md: "h-[112px] w-auto max-w-[240px] object-cover", lg: "h-[140px] w-auto max-w-[280px] object-cover" };

export function MediaStrip({ media, size = "lg" }: { media: XMedia[]; size?: keyof typeof MEDIA_HEIGHT }) {
  if (!media.length) return null;
  return (
    <div className="flex gap-2 flex-wrap mt-2">
      {media.map((m) => (
        <a
          key={m.tco + m.image}
          href={m.url}
          target="_blank"
          rel="noopener noreferrer"
          className="relative block overflow-hidden rounded-[var(--sn-radius-xs)] border"
          style={{ borderColor: "var(--sn-border-subtle)" }}
        >
          <img src={`${m.image}?name=small`} alt="" loading="lazy" className={MEDIA_HEIGHT[size]} />
          {m.type !== "photo" && (
            <span className="absolute inset-0 flex items-center justify-center bg-black/30">
              <Play size={size === "sm" ? 16 : 24} className="text-white" />
            </span>
          )}
        </a>
      ))}
    </div>
  );
}

/** A quoted post in an inset box. `compact` shortens it to a few lines without media. */
export function QuotedPost({ quoted, compact = false }: { quoted: XQuoted; compact?: boolean }) {
  return (
    <div
      className="mt-3 rounded-[var(--sn-radius-sm)] border p-[12px_14px]"
      style={{ borderColor: "var(--sn-border)", background: "var(--sn-surface-3)" }}
    >
      <a
        href={quoted.url}
        target="_blank"
        rel="noopener noreferrer"
        className="text-[12px] font-semibold text-[var(--sn-text)] hover:text-[var(--sn-accent)] transition-colors"
      >
        @{quoted.author}
      </a>
      <p
        className={`text-[13px] text-[var(--sn-text-secondary)] leading-[1.6] whitespace-pre-line break-words mt-1 ${
          compact ? "line-clamp-3" : "line-clamp-6"
        }`}
      >
        <PostText text={quoted.text} links={quoted.links} media={quoted.media} />
      </p>
      {!compact && <MediaStrip media={quoted.media} size="sm" />}
    </div>
  );
}
