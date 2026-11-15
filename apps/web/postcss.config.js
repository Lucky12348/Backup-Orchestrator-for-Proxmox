// Tailwind only. No autoprefixer on purpose: it would rewrite the legacy
// styles.css (vendor prefixes) and the existing pages must stay byte-identical.
export default {
  plugins: {
    tailwindcss: {},
  },
};
