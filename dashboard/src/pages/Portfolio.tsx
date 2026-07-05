import { Card } from "@/components/layout/Card";

/** Stub page — to be implemented. */
export function PortfolioPage() {
  return (
    <Card title="Portfolio" extra={<span>coming soon</span>}>
      <div className="alert alert-info bg-base-300 border-base-300">
        <p className="text-base-content/60 italic py-4">
          The full Portfolio page (allocation pie, exposure bars, VaR
          distribution) is a stub. Use the Dashboard page for now — it surfaces
          account-level metrics and the equity curve.
        </p>
      </div>
    </Card>
  );
}
