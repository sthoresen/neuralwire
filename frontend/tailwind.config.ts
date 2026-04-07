import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class", '[data-theme="dark"]'],
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ["DM Sans", "sans-serif"],
        mono: ["JetBrains Mono", "monospace"],
        serif: ["Fraunces", "serif"],
      },
      colors: {
        sn: {
          bg: "var(--sn-bg)",
          surface: "var(--sn-surface)",
          "surface-2": "var(--sn-surface-2)",
          "surface-3": "var(--sn-surface-3)",
          border: "var(--sn-border)",
          "border-subtle": "var(--sn-border-subtle)",
          text: "var(--sn-text)",
          "text-secondary": "var(--sn-text-secondary)",
          "text-tertiary": "var(--sn-text-tertiary)",
          red: "var(--sn-red)",
          "red-dim": "var(--sn-red-dim)",
          amber: "var(--sn-amber)",
          accent: "var(--sn-accent)",
          "accent-dim": "var(--sn-accent-dim)",
          "accent-glow": "var(--sn-accent-glow)",
        },
      },
      borderRadius: {
        sn: "12px",
        "sn-sm": "8px",
        "sn-xs": "6px",
      },
    },
  },
  plugins: [],
};

export default config;
