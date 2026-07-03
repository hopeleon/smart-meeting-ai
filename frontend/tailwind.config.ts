import type { Config } from 'tailwindcss'

export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        primary: '#3B82F6',
        dark: {
          DEFAULT: '#0F172A',
          100: '#1E293B',
          200: '#334155',
          300: '#475569',
        },
      },
    },
  },
  plugins: [],
} satisfies Config
