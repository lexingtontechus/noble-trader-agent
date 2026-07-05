import { Navigate, Route, Routes } from "react-router-dom";
import { Navbar } from "@/components/layout/Navbar";
import { Footer } from "@/components/layout/Footer";
import { useAuth } from "@/lib/auth";
import { DashboardPage } from "@/pages/Dashboard";
import { StatusPage } from "@/pages/Status";
import { SymbolsPage } from "@/pages/Symbols";
import { MonitorPage } from "@/pages/Monitor";
import { PnLPage } from "@/pages/PnL";
import { PortfolioPage } from "@/pages/Portfolio";
import { BacktestPage } from "@/pages/Backtest";
import { LoginPage } from "@/pages/Login";

export function App() {
  const { isAuthenticated, isLoading } = useAuth();

  // Wait for the /auth/me check to complete before deciding what to render.
  // This prevents a flash of the login page on every refresh.
  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <span className="loading loading-spinner loading-lg text-primary" />
      </div>
    );
  }

  if (!isAuthenticated) {
    return <LoginPage />;
  }

  return (
    <div className="min-h-screen flex flex-col">
      <Navbar />
      <main className="container mx-auto px-4 py-6 max-w-7xl flex-1">
        <Routes>
          {/* Home = Dashboard (account + PnL) */}
          <Route path="/" element={<DashboardPage />} />
          <Route path="/status" element={<StatusPage />} />
          <Route path="/monitor" element={<MonitorPage />} />
          <Route path="/symbols" element={<SymbolsPage />} />
          <Route path="/pnl" element={<PnLPage />} />
          <Route path="/portfolio" element={<PortfolioPage />} />
          <Route path="/backtest" element={<BacktestPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      <Footer />
    </div>
  );
}
