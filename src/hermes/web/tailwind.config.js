/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./templates/**/*.html",
    "./static/**/*.js",
  ],
  theme: {
    extend: {},
  },
  plugins: [require("daisyui")],
  daisyui: {
    logs: false,
    themes: ["dark", "retro", "cyberpunk", "nord", "dracula", "synthwave", "light"],
  },
};
