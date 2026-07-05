/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  // Match the existing Jinja2 dashboard's theme list (src/hermes/web/templates/base.html)
  plugins: [require("daisyui")],
  daisyui: {
    themes: [
      "dark",
      "retro",
      "cyberpunk",
      "nord",
      "dracula",
      "synthwave",
      "light",
    ],
    logs: false,
  },
};
