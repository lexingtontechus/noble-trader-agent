"use client";

import Link from "next/link";
import { useState } from "react";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth-simple";
import  ThemeSwitcher  from "./ThemeSwitcher";

const NAV_ITEMS = [
  { href: "/", label: "Dashboard" },
  { href: "/status", label: "Status" },
  { href: "/monitor", label: "Monitor" },
  { href: "/symbols", label: "Symbols" },
  { href: "/pnl", label: "PnL" },
  { href: "/portfolio", label: "Portfolio" },
  { href: "/backtest", label: "Backtest" },
  { href: "/agent", label: "Agent" },
];

export function Navbar() {
  const { isAuthenticated, logout, user } = useAuth();
  const pathname = usePathname();

  const handleLogout = async () => {
    await logout();
    window.location.href = "/";
  };

  return (
    <div className="navbar bg-base-200 shadow-lg sticky top-0 z-50 uppercase">
      <div className="navbar-start">
        <div className="dropdown lg:hidden">
          <div
            tabIndex={0}
            role="button"
            className="btn btn-ghost btn-sm"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className="h-5 w-5"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M4 6h16M4 12h16M4 18h7"
              />
            </svg>
          </div>
          <ul
            tabIndex={0}
            className="menu menu-sm dropdown-content mt-3 z-[1] p-2 shadow bg-base-200 rounded-box w-52 $ hidden"
          >
            {NAV_ITEMS.map((item) => (
              <li key={item.href}>
                <Link
                  href={item.href}

                  className={pathname === item.href ? "active" : ""}
                >
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
        </div>

        <Link href="/" className="btn btn-ghost btn-sm text-lg font-bold">
          Noble Trader
          <span className="badge badge-primary badge-xs ml-1">v0.1</span>
        </Link>
      </div>

      <div className="navbar-center hidden lg:flex">
        <ul className="menu menu-horizontal menu-sm px-1">
          {NAV_ITEMS.map((item) => (
            <li key={item.href}>
              <Link
                href={item.href}
                className={pathname === item.href ? "active" : ""}
              >
                {item.label}
              </Link>
            </li>
          ))}
        </ul>
      </div>

      <div className="navbar-end gap-2">
        {user && (
          <span className="text-xs opacity-50 hidden sm:inline">
            {user.username}
          </span>
        )}
        <ThemeSwitcher />
        {isAuthenticated ? (
          <button
            className="btn btn-ghost btn-sm"
            onClick={handleLogout}
            title="Sign out"
          >
            Sign out
          </button>
        ) : null}
      </div>
    </div>
  );
}
