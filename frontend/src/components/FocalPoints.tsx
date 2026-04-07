"use client";

import { useState } from "react";
import { ChevronDown } from "lucide-react";

interface FocalPoint {
  q: string;
  a: string;
}

interface Artefact {
  content: string | null;
  generated_at: string | null;
  model_name: string | null;
}

function parse(content: string): FocalPoint[] {
  const items: FocalPoint[] = [];
  const blocks = content.trim().split(/\n(?=\*\*)/);
  for (const block of blocks) {
    const b = block.trim();
    if (!b) continue;
    const parts = b.split("\n");
    const title = parts[0].replace(/^\*+|\*+$/g, "").trim();
    const body = parts.slice(1).join("\n").trim();
    if (title) items.push({ q: title, a: body });
  }
  return items;
}

function mdInline(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>");
}

function AccordionItem({ item }: { item: FocalPoint }) {
  const [open, setOpen] = useState(false);
  return (
    <div
      className="rounded-[var(--sn-radius)] border mb-[10px] overflow-hidden transition-colors"
      style={{
        background: "var(--sn-surface)",
        borderColor: "var(--sn-border-subtle)",
      }}
    >
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-6 py-[18px] text-left"
      >
        <span className="font-serif text-[15px] font-medium text-[var(--sn-text)] leading-[1.45]">
          {item.q}
        </span>
        <ChevronDown
          size={16}
          className="shrink-0 ml-4 text-[var(--sn-text-tertiary)] transition-transform duration-200"
          style={{ transform: open ? "rotate(180deg)" : "rotate(0deg)" }}
        />
      </button>
      {open && (
        <div className="px-6 pb-5">
          <p
            className="text-[13.5px] text-[var(--sn-text-secondary)] leading-[1.75] font-light"
            dangerouslySetInnerHTML={{ __html: mdInline(item.a) }}
          />
        </div>
      )}
    </div>
  );
}

interface Props {
  artefact: Artefact | null;
}

export default function FocalPoints({ artefact }: Props) {
  if (!artefact?.content) {
    return (
      <p className="text-[var(--sn-text-secondary)] text-sm italic">
        No focal points yet.
      </p>
    );
  }

  const items = parse(artefact.content);

  if (!items.length) {
    return (
      <p className="text-[var(--sn-text-secondary)] text-sm italic">
        No focal points yet.
      </p>
    );
  }

  return (
    <div>
      {items.map((item, i) => (
        <AccordionItem key={i} item={item} />
      ))}
      {artefact.generated_at && (
        <div className="font-mono text-[11px] text-[var(--sn-text-tertiary)] mt-2">
          Generated {artefact.generated_at.slice(0, 10)}
          {artefact.model_name ? ` · ${artefact.model_name}` : ""}
        </div>
      )}
    </div>
  );
}
