import type { ReactNode } from "react";
import { cn } from "@/lib/format";

interface CardProps {
  title?: string;
  extra?: ReactNode;
  className?: string;
  children?: ReactNode;
}

export function Card({ title, extra, className, children }: CardProps) {
  return (
    <div className={cn("card bg-base-200 shadow-xl mb-4", className)}>
      <div className="card-body">
        {title && (
          <>
            <h2 className="card-title text-lg font-semibold">{title}</h2>
            <div className="divider my-1" />
          </>
        )}
        {extra && (
          <div className="text-xs opacity-50 mb-2 flex justify-between items-center">
            {extra}
          </div>
        )}
        {children}
      </div>
    </div>
  );
}
