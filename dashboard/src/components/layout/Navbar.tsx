import { useEffect, useState } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "@/lib/auth";
import { ThemeSwitcher } from "./ThemeSwitcher";

const NAV_ITEMS = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/status", label: "Status" },
  { to: "/monitor", label: "Monitor" },
  { to: "/symbols", label: "Symbols" },
  { to: "/pnl", label: "PnL" },
  { to: "/portfolio", label: "Portfolio" },
  { to: "/backtest", label: "Backtest" },
  { to: "/agent", label: "Agent" },
];

export function Navbar() {
  const location = useLocation();
  const navigate = useNavigate();
  const { isAuthenticated, logout, user } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);

  // Close mobile menu on route change
  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  const handleLogout = async () => {
    await logout();
    navigate("/"); // Will redirect to login via App.tsx
  };

  return (
    <div className="navbar bg-base-200 shadow-lg sticky top-0 z-50">
      <div className="navbar-start">
        {/* Mobile menu */}
        <div className="dropdown lg:hidden">
          <div
            tabIndex={0}
            role="button"
            className="btn btn-ghost btn-sm"
            onClick={() => setMobileOpen((o) => !o)}
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
          {mobileOpen && (
            <ul
              tabIndex={0}
              className="menu menu-sm dropdown-content mt-3 z-[1] p-2 shadow bg-base-200 rounded-box w-52"
            >
              {NAV_ITEMS.map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    end={item.end}
                    className={({ isActive }) =>
                      isActive ? "active" : ""
                    }
                  >
                    {item.label}
                  </NavLink>
                </li>
              ))}
            </ul>
          )}
        </div>

        <Link to="/" className="btn btn-ghost btn-sm text-lg font-bold">
          Hermes
          <span className="badge badge-primary badge-xs ml-1">v0.1</span>
        </Link>
      </div>

      {/* Desktop nav */}
      <div className="navbar-center hidden lg:flex">
        <ul className="menu menu-horizontal menu-sm px-1">
          {NAV_ITEMS.map((item) => (
            <li key={item.to}>
              <NavLink
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  isActive ? "active" : ""
                }
              >
                {item.label}
              </NavLink>
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
