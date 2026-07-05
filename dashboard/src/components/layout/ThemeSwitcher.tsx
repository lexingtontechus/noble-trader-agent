import { useEffect, useState } from "react";

const THEMES = [
  { id: "dark", label: "Dark", icon: "🌙" },
  { id: "retro", label: "Retro", icon: "📺" },
  { id: "cyberpunk", label: "Cyberpunk", icon: "🤖" },
  { id: "nord", label: "Nord", icon: "❄️" },
  { id: "dracula", label: "Dracula", icon: "🧛" },
  { id: "synthwave", label: "Synthwave", icon: "🌆" },
  { id: "light", label: "Light", icon: "☀️" },
];

const STORAGE_KEY = "hermes-theme";

export function ThemeSwitcher() {
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState<string>(() => {
    return localStorage.getItem(STORAGE_KEY) || "dark";
  });

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", current);
  }, [current]);

  const setTheme = (theme: string) => {
    setCurrent(theme);
    localStorage.setItem(STORAGE_KEY, theme);
    setOpen(false);
  };

  return (
    <div className="dropdown dropdown-end">
      <div
        tabIndex={0}
        role="button"
        className="btn btn-ghost btn-sm"
        onClick={() => setOpen((o) => !o)}
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          width={16}
          height={16}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
        >
          <circle cx="12" cy="12" r="5" />
          <line x1="12" y1="1" x2="12" y2="3" />
          <line x1="12" y1="21" x2="12" y2="23" />
          <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
          <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
          <line x1="1" y1="12" x2="3" y2="12" />
          <line x1="21" y1="12" x2="23" y2="12" />
          <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
          <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
        </svg>
        Theme
      </div>
      {open && (
        <ul
          tabIndex={0}
          className="dropdown-content z-[1] menu p-2 shadow-lg bg-base-200 rounded-box w-48"
        >
          {THEMES.map((t) => (
            <li key={t.id}>
              <button
                onClick={() => setTheme(t.id)}
                className={current === t.id ? "active" : ""}
              >
                <span>{t.icon}</span>
                <span>{t.label}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
