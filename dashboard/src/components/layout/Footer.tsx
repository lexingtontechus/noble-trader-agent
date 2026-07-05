export function Footer() {
  return (
    <footer className="footer footer-center p-4 text-xs opacity-50 border-t border-base-300">
      <aside>
        Hermes Trading Platform ·{" "}
        <a
          href="/health"
          target="_blank"
          rel="noreferrer"
          className="link link-hover"
        >
          Health JSON
        </a>{" "}
        ·{" "}
        <a
          href="/api/status"
          target="_blank"
          rel="noreferrer"
          className="link link-hover"
        >
          API Status
        </a>
      </aside>
    </footer>
  );
}
