"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Show, UserButton, useUser } from "@clerk/nextjs";
import styles from "./AppHeader.module.css";

export default function AppHeader() {
  const { user } = useUser();
  const pathname = usePathname();

  return (
    <header className={styles.header}>
      <div className={styles.left}>
        <div className={styles.logoMark}>
          <svg viewBox="0 0 20 20" fill="none">
            <path
              d="M3 14L7 9L11 11L17 5"
              stroke="#0b0c0f"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <circle cx="17" cy="5" r="2" fill="#0b0c0f" />
          </svg>
        </div>
        <div>
          <h1 className={styles.title}>Noble Trader Agent — Risk Manager</h1>
          <div className={styles.statusPill}>v2.1 · LIVE</div>
        </div>
      </div>

      <div className={styles.right}>
        {/* Nav links */}
        <Show when="signed-in">
          <nav className={styles.nav}>
            <Link
              href="/history"
              className={`${styles.navLink} ${
                pathname === "/history" ? styles.navLinkActive : ""
              }`}
            >
              <div className="btn btn-secondary text-sm uppercase px-2">
                History
              </div>
            </Link>
          </nav>

          <UserButton
            appearance={{
              elements: {
                avatarBox: { width: "32px", height: "32px" },
                userButtonPopoverCard: {
                  background: "var(--surface)",
                  border: "1px solid var(--border-accent)",
                  borderRadius: "10px",
                },
                userButtonPopoverActionButton: {
                  color: "var(--text)",
                  fontFamily: "var(--font-mono)",
                  fontSize: "13px",
                },
                userButtonPopoverActionButton__signOut: { color: "var(--red)" },
                userButtonPopoverFooter: { display: "none" },
              },
            }}
          />
        </Show>
      </div>
    </header>
  );
}
