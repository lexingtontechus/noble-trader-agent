import Link from "next/link";
export default function Footer() {
  return (
    <footer className="footer sm:footer-horizontal footer-center p-4">
      <nav className="mx-auto grid grid-flow-col gap-4">
        <Link href="/terms">Terms</Link>
        <Link href="/privacy">Privacy</Link>
      </nav>
      <aside>
        <p className="px-2">
          Copyright © {new Date().getFullYear()}{" "}
          <span className="uppercase">Noble Trading App</span>
        </p>
      </aside>
    </footer>
  );
}