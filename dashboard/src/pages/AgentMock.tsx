"use client";
import { useEffect, useState } from "react";
import { Card } from "@/components/layout/Card";
import { getHypotheses, getDecisionTree, getTradeJournal } from "@/lib/api-mock";

interface Hypothesis {
  id: string;
  title: string;
  confidence: number;
  status: "active" | "testing" | "completed";
  created_at: string;
}

interface DecisionNode {
  id: string;
  name: string;
  children?: DecisionNode[];
  type: "decision" | "action" | "result";
}

interface JournalEntry {
  id: number;
  timestamp: string;
  action: string;
  symbol: string;
  result: string;
  pnl: number;
}

export function AgentPage() {
  const [hypotheses, setHypotheses] = useState<Hypothesis[]>([]);
  const [decisionTree, setDecisionTree] = useState<DecisionNode | null>(null);
  const [tradeJournal, setTradeJournal] = useState<JournalEntry[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [hypoRes, treeRes, journalRes] = await Promise.all([
          getHypotheses(10),
          getDecisionTree(),
          getTradeJournal(20)
        ]);
        
        setHypotheses(hypoRes.hypotheses);
        setDecisionTree(treeRes.decision_tree);
        setTradeJournal(journalRes.trades);
      } catch (error) {
        console.error("Failed to load agent data:", error);
      } finally {
        setIsLoading(false);
      }
    };

    fetchData();
  }, []);

  const getStatusColor = (status: string) => {
    switch (status) {
      case "active": return "badge-success";
      case "testing": return "badge-warning";
      case "completed": return "badge-info";
      default: return "badge";
    }
  };

  const formatCurrency = (value: number) => {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 2,
    }).format(value);
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <span className="loading loading-spinner loading-lg text-primary" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">AI Agent</h1>
        <p className="text-base-content opacity-70">Trading strategies and decision making</p>
      </div>

      {/* Hypotheses */}
      <Card title="Active Strategies">
        <div className="space-y-4">
          {hypotheses.map((hypothesis) => (
            <div key={hypothesis.id} className="border rounded-lg p-4">
              <div className="flex justify-between items-start mb-2">
                <h3 className="font-bold">{hypothesis.title}</h3>
                <span className={`badge ${getStatusColor(hypothesis.status)}`}>
                  {hypothesis.status}
                </span>
              </div>
              <div className="flex justify-between items-center text-sm opacity-70">
                <span>Confidence: {(hypothesis.confidence * 100).toFixed(1)}%</span>
                <span>Created: {new Date(hypothesis.created_at).toLocaleDateString()}</span>
              </div>
            </div>
          ))}
        </div>
      </Card>

      {/* Decision Tree */}
      <Card title="Decision Tree">
        <div className="h-96">
          {decisionTree && (
            <div className="p-4">
              <div className="text-center font-bold mb-4">{decisionTree.name}</div>
              <div className="flex justify-center">
                <div className="border-2 border-primary rounded-lg p-4 max-w-md">
                  <div className="text-center font-bold">{decisionTree.name}</div>
                  {decisionTree.children && decisionTree.children.map((child) => (
                    <div key={child.id} className="mt-2">
                      <div className="font-medium">{child.name}</div>
                      {child.children && child.children.map((grandchild) => (
                        <div key={grandchild.id} className="ml-4 text-sm opacity-70">
                          {grandchild.name}
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      </Card>

      {/* Trade Journal */}
      <Card title="Trade Journal">
        <div className="overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>Timestamp</th>
                <th>Action</th>
                <th>Symbol</th>
                <th>Result</th>
                <th>PnL</th>
              </tr>
            </thead>
            <tbody>
              {tradeJournal.map((entry) => (
                <tr key={entry.id}>
                  <td className="font-mono text-sm">
                    {new Date(entry.timestamp).toLocaleDateString()} {new Date(entry.timestamp).toLocaleTimeString()}
                  </td>
                  <td className="font-mono">{entry.action}</td>
                  <td className="font-mono">{entry.symbol}</td>
                  <td>{entry.result}</td>
                  <td className={entry.pnl >= 0 ? "text-success" : "text-error"}>
                    {entry.pnl >= 0 ? '+' : ''}{formatCurrency(entry.pnl)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}