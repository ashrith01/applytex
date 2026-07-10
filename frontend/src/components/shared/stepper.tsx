import { cn } from "@/lib/utils";

interface StepperProps {
  steps: string[];
  current: number;
}

export function Stepper({ steps, current }: StepperProps) {
  return (
    <div className="flex flex-wrap items-center gap-2 border-y border-border py-3">
      {steps.map((label, index) => {
        const step = index + 1;
        const active = step === current;
        const done = step < current;
        return (
          <div key={label} className="flex items-center gap-2">
            {index > 0 && <span className="font-mono text-ink-muted">/</span>}
            <div
              className={cn(
                "inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm font-semibold",
                active && "border-primary/30 bg-surface-source text-ink",
                done && "border-primary/20 bg-surface-card text-primary",
                !active && !done && "border-border bg-surface-card text-ink-muted",
              )}
            >
              <span
                className={cn(
                  "flex h-6 w-6 items-center justify-center rounded-sm font-mono text-xs font-black",
                  active && "bg-primary text-primary-foreground",
                  done && "bg-primary/15 text-primary",
                  !active && !done && "bg-surface-muted text-ink-muted",
                )}
              >
                {step}
              </span>
              {label}
            </div>
          </div>
        );
      })}
    </div>
  );
}
