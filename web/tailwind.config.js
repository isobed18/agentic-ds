/** Design tokens lifted from design_assets/ — light product surface, blue accent. */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: "#0f172a", soft: "#334155", mute: "#64748b", faint: "#94a3b8" },
        line: { DEFAULT: "#e2e8f0", soft: "#f1f5f9" },
        surface: { DEFAULT: "#ffffff", sunken: "#f8fafc", raised: "#ffffff" },
        // #196: every stop of each scale is defined, not only the handful the
        // first screens happened to use. A utility for a missing stop emits no
        // rule at all and Tailwind reports nothing, so `bg-ok-300` on the
        // completed-stage arrow silently drew no line and read as a design bug.
        // Shades are the Tailwind defaults these four were lifted from --
        // blue, emerald, amber, red -- so the existing stops keep their values.
        brand: { 50:"#eff6ff",100:"#dbeafe",200:"#bfdbfe",300:"#93c5fd",400:"#60a5fa",500:"#3b82f6",600:"#2563eb",700:"#1d4ed8",800:"#1e40af",900:"#1e3a8a" },
        ok: { 50:"#ecfdf5",100:"#d1fae5",200:"#a7f3d0",300:"#6ee7b7",400:"#34d399",500:"#10b981",600:"#059669",700:"#047857",800:"#065f46",900:"#064e3b" },
        warn: { 50:"#fffbeb",100:"#fef3c7",200:"#fde68a",300:"#fcd34d",400:"#fbbf24",500:"#f59e0b",600:"#d97706",700:"#b45309",800:"#92400e",900:"#78350f" },
        stop: { 50:"#fef2f2",100:"#fee2e2",200:"#fecaca",300:"#fca5a5",400:"#f87171",500:"#ef4444",600:"#dc2626",700:"#b91c1c",800:"#991b1b",900:"#7f1d1d" },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: {
        card: "0 1px 2px 0 rgb(15 23 42 / 0.04), 0 1px 3px 0 rgb(15 23 42 / 0.06)",
        pop: "0 4px 16px -2px rgb(15 23 42 / 0.10), 0 2px 6px -2px rgb(15 23 42 / 0.06)",
      },
      borderRadius: { xl: "0.75rem", "2xl": "1rem" },
    },
  },
  plugins: [],
};
