/** Design tokens lifted from design_assets/ — light product surface, blue accent. */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: "#0f172a", soft: "#334155", mute: "#64748b", faint: "#94a3b8" },
        line: { DEFAULT: "#e2e8f0", soft: "#f1f5f9" },
        surface: { DEFAULT: "#ffffff", sunken: "#f8fafc", raised: "#ffffff" },
        brand: { 50:"#eff6ff",100:"#dbeafe",200:"#bfdbfe",500:"#3b82f6",600:"#2563eb",700:"#1d4ed8" },
        ok: { 50:"#ecfdf5",500:"#10b981",600:"#059669",700:"#047857" },
        warn: { 50:"#fffbeb",500:"#f59e0b",600:"#d97706",700:"#b45309" },
        stop: { 50:"#fef2f2",500:"#ef4444",600:"#dc2626",700:"#b91c1c" },
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
