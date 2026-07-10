import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        primary: {
          DEFAULT: "#177A55",
          foreground: "#fbfff9",
        },
        accent: {
          DEFAULT: "#2457A6",
          foreground: "#ffffff",
        },
        budget: {
          DEFAULT: "#B56A14",
          soft: "#FFF3DD",
        },
        surface: {
          DEFAULT: "#FBFAF7",
          card: "#ffffff",
          muted: "#F0EFE8",
          source: "#EDF5EF",
        },
        border: {
          DEFAULT: "#DEDAD0",
        },
        ink: {
          DEFAULT: "#17201B",
          muted: "#5D645F",
          subtle: "#7A817A",
        },
      },
      borderRadius: {
        card: "8px",
        pill: "999px",
      },
      fontFamily: {
        sans: ["var(--font-ui)", "Helvetica Neue", "Arial", "sans-serif"],
        serif: ["var(--font-display)", "Source Serif 4", "Iowan Old Style", "Georgia", "serif"],
        mono: ["var(--font-mono)", "JetBrains Mono", "SFMono-Regular", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
