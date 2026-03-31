"use client";
import { UserButton, useUser } from "@clerk/nextjs";
//import styles from "./AppHeader.module.css";

export default function AppHeader() {
  const { user } = useUser();
  return (
    <div className="navbar bg-base-100 shadow-sm">
      <div className="flex-1">
        <Logo />
        <h1 className="">Noble Trading — Risk Manager</h1>
      </div>
      <div className="flex-none uppercase ml-2 px-2 w-48">
        {/*  {user && (
          <span className={styles.userName}>
            {user.firstName || user.emailAddresses[0]?.emailAddress}
          </span>
        )}*/}
        <div className="px-4 bg-green-950 text-green-400">v2.1 · LIVE</div>
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
              userButtonPopoverActionButton__signOut: {
                color: "var(--red)",
              },
              userButtonPopoverFooter: { display: "none" },
            },
          }}
        />
      </div>
    </div>
  );
}

function Logo() {
  return (
    <svg
      viewBox="0 0 20 20"
      fill="none"
      className="btn btn-circle size-8 px-4 bg-[#c8f542]"
    >
      <path
        d="M3 14L7 9L11 11L17 5"
        stroke="#0b0c0f"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="17" cy="5" r="2" fill="#0b0c0f" />
    </svg>
  );
}
