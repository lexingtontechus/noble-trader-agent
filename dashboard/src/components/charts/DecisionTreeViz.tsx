import { useState } from "react";
import type { DecisionTreeNode } from "@/lib/types";
import { cn } from "@/lib/format";

interface DecisionTreeVizProps {
  node: DecisionTreeNode;
  /** Path of branch keys taken to reach the currently active node, e.g. ["yes", "no"]. */
  activePath?: string[];
}

const colorMap: Record<string, string> = {
  error: "border-error bg-error/10 text-error",
  success: "border-success bg-success/10 text-success",
  warning: "border-warning bg-warning/10 text-warning",
  info: "border-info bg-info/10 text-info",
  primary: "border-primary bg-primary/10 text-primary",
  neutral: "border-base-300 bg-base-300/30 text-base-content",
};

/** Recursively renders the decision tree as nested boxes with collapsible branches. */
export function DecisionTreeViz({ node, activePath = [] }: DecisionTreeVizProps) {
  return <TreeNode node={node} depth={0} currentPath="" activePath={activePath} />;
}

interface TreeNodeProps {
  node: DecisionTreeNode;
  depth: number;
  currentPath: string;
  activePath: string[];
}

function TreeNode({ node, depth, currentPath, activePath }: TreeNodeProps) {
  const [collapsed, setCollapsed] = useState(depth > 1);
  const isActive =
    activePath.length > 0 &&
    (currentPath === activePath.slice(0, depth).join(".") ||
      currentPath === activePath.join("."));

  const colorClass = node.color
    ? colorMap[node.color] || colorMap.neutral
    : colorMap.neutral;

  const branches = node.branches
    ? Object.entries(node.branches)
    : [];

  return (
    <div className="flex flex-col items-center">
      <button
        onClick={() => branches.length > 0 && setCollapsed((c) => !c)}
        className={cn(
          "border-2 rounded-lg px-4 py-2 text-center transition-all max-w-md",
          colorClass,
          isActive && "ring-2 ring-offset-2 ring-offset-base-100 ring-primary",
          branches.length > 0 && "cursor-pointer hover:scale-105",
        )}
        style={{ minWidth: 180 }}
      >
        <div className="flex items-center justify-center gap-2">
          {node.icon && <span className="text-lg">{node.icon}</span>}
          <span className="font-semibold text-sm">{node.label}</span>
          {branches.length > 0 && (
            <span className="text-xs opacity-60">
              {collapsed ? "▸" : "▾"}
            </span>
          )}
        </div>
        {node.question && (
          <div className="text-xs opacity-70 mt-1">{node.question}</div>
        )}
        {node.thresholds && Object.keys(node.thresholds).length > 0 && (
          <div className="text-xs font-mono opacity-60 mt-1">
            {Object.entries(node.thresholds).map(([k, v]) => (
              <span key={k} className="mr-2">
                {k}={v}
              </span>
            ))}
          </div>
        )}
      </button>

      {branches.length > 0 && !collapsed && (
        <div className="flex gap-4 mt-4">
          {branches.map(([branchKey, child]) => {
            const childPath = currentPath
              ? `${currentPath}.${branchKey}`
              : branchKey;
            return (
              <div
                key={branchKey}
                className="flex flex-col items-center relative"
              >
                {/* Connector line */}
                <div className="w-px h-4 bg-base-300" />
                <div className="text-xs opacity-60 mb-2 font-mono uppercase">
                  {branchKey}
                </div>
                <TreeNode
                  node={child}
                  depth={depth + 1}
                  currentPath={childPath}
                  activePath={activePath}
                />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
