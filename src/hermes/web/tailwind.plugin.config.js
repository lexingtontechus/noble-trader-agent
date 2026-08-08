/** @type {import('tailwindcss').Config} */
// Plugin-scoped Tailwind + DaisyUI build for the Hermes DESKTOP plugins
// (talaria + noble-trader-admin).
//
// WHY prefix "dui-": the Hermes desktop app itself uses Tailwind v4
// (@import 'tailwindcss' in apps/desktop/src/styles.css) with classes like
// `.card`, `.table`, `.flex`, `.badge`. Injecting an UNPREFIXED daisyUI
// bundle into the app DOM would collide with the app's own Tailwind classes
// (same names, different definitions → visual breakage app-wide). The
// `dui-` prefix namespaces every daisyUI component class (`.dui-btn`,
// `.dui-card`, `.dui-badge`, ...) so the plugin can use daisyUI components
// without touching the host app.
//
// Also NO `@tailwind base` in the input CSS: the preflight/base reset would
// clobber the app's global styles inside the Electron renderer. Only
// components + utilities are emitted, and `content` scans the two plugin
// source trees so the bundle contains ONLY the daisyUI classes the plugins
// actually use (32KB today vs 77KB for the web dashboard's full build).
module.exports = {
  prefix: "dui-",
  content: [
    "../../../.hermes/plugins/talaria/**/*.js",
    "../../../.hermes/plugins/noble-trader-admin/**/*.js",
  ],
  theme: {
    extend: {},
  },
  plugins: [require("daisyui")],
  daisyui: {
    logs: false,
    themes: ["dark"],
  },
};
