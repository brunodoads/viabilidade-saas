import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        border: 'hsl(214.3 31.8% 91.4%)',
        background: 'hsl(0 0% 100%)',
        foreground: 'hsl(222.2 84% 4.9%)',
        muted: { DEFAULT: 'hsl(210 40% 96.1%)', foreground: 'hsl(215.4 16.3% 46.9%)' },
        primary: { DEFAULT: 'hsl(221.2 83.2% 53.3%)', foreground: 'hsl(210 40% 98%)' },
        destructive: { DEFAULT: 'hsl(0 84.2% 60.2%)', foreground: 'hsl(210 40% 98%)' },
      },
      borderRadius: { lg: '0.5rem', md: '0.375rem', sm: '0.25rem' },
    },
  },
  plugins: [],
}
export default config
