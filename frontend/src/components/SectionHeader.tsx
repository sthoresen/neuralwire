interface Props {
  label: string;
  mt?: string;
}

export default function SectionHeader({ label, mt = "48px" }: Props) {
  return (
    <div
      className="flex items-center gap-3"
      style={{ marginTop: mt, marginBottom: 20 }}
    >
      <span className="font-mono text-[10px] text-[var(--sn-text-secondary)] uppercase tracking-[0.14em] whitespace-nowrap">
        {label}
      </span>
      <div className="flex-1 h-px bg-[var(--sn-border-subtle)]" />
    </div>
  );
}
