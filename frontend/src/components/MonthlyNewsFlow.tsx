interface Artefact {
  content: string | null;
  generated_at: string | null;
  model_name: string | null;
}

function mdInline(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>");
}

function renderParas(content: string, limit?: number) {
  const paras = content.split("\n\n").map((p) => p.trim()).filter(Boolean);
  const shown = limit ? paras.slice(0, limit) : paras;
  return shown.map((p, i) => (
    <p
      key={i}
      className="mb-4 last:mb-0"
      dangerouslySetInnerHTML={{ __html: mdInline(p) }}
    />
  ));
}

function Meta({ artefact }: { artefact: Artefact }) {
  if (!artefact.generated_at) return null;
  return (
    <div className="font-mono text-[11px] text-[var(--sn-text-tertiary)] mt-4">
      Generated {artefact.generated_at.slice(0, 10)}
      {artefact.model_name ? ` · ${artefact.model_name}` : ""}
    </div>
  );
}

interface Props {
  artefact: Artefact | null;
}

export default function MonthlyNewsFlow({ artefact }: Props) {
  const empty = (
    <p className="text-[var(--sn-text-secondary)] text-sm italic">
      No monthly news flow yet.
    </p>
  );

  return (
    <div className="grid grid-cols-2 gap-5" style={{ marginTop: 56 }}>
      {/* Left: preview (first 2 paras) */}
      <div>
        <div className="flex items-center gap-3 mb-5">
          <span className="font-mono text-[10px] text-[var(--sn-text-tertiary)] uppercase tracking-[0.14em] whitespace-nowrap">
            Monthly News Flow
          </span>
          <div className="flex-1 h-px bg-[var(--sn-border-subtle)]" />
        </div>
        <div
          className="rounded-[var(--sn-radius)] p-7 border transition-colors"
          style={{
            background: "var(--sn-surface)",
            borderColor: "var(--sn-border-subtle)",
          }}
        >
          <div className="text-[14px] text-[var(--sn-text-secondary)] leading-[1.8] font-light">
            {artefact?.content ? renderParas(artefact.content, 2) : empty}
          </div>
          {artefact && <Meta artefact={artefact} />}
        </div>
      </div>

      {/* Right: full content */}
      <div>
        <div className="flex items-center gap-3 mb-5">
          <span className="font-mono text-[10px] text-[var(--sn-text-tertiary)] uppercase tracking-[0.14em] whitespace-nowrap">
            What&apos;s Moving the Stock
          </span>
          <div className="flex-1 h-px bg-[var(--sn-border-subtle)]" />
        </div>
        <div
          className="rounded-[var(--sn-radius)] p-7 border transition-colors"
          style={{
            background: "var(--sn-surface)",
            borderColor: "var(--sn-border-subtle)",
          }}
        >
          <div className="text-[14px] text-[var(--sn-text-secondary)] leading-[1.8] font-light">
            {artefact?.content ? renderParas(artefact.content) : empty}
          </div>
          {artefact && <Meta artefact={artefact} />}
        </div>
      </div>
    </div>
  );
}
