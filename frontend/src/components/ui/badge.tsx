import { cn } from "@/lib/utils";
import { HTMLAttributes } from "react";

export function Badge({
  className,
  tone = "default",
  ...props
}: HTMLAttributes<HTMLSpanElement> & { tone?: "default" | "success" | "warning" | "muted" }) {
  const tones = {
    default: "bg-surface-muted text-ink",
    success: "bg-surface-source text-primary border-primary/20",
    warning: "bg-budget-soft text-budget border-budget/20",
    muted: "bg-white text-ink-muted border-border",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-pill border px-3 py-1 text-xs font-semibold",
        tones[tone],
        className,
      )}
      {...props}
    />
  );
}
