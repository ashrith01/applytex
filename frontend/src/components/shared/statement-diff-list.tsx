import type { StatementDiff } from "@/lib/api/types";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";

interface StatementDiffListProps {
  diffs: StatementDiff[];
}

export function StatementDiffList({ diffs }: StatementDiffListProps) {
  if (diffs.length === 0) {
    return <p className="text-sm text-ink-muted">No statement changes yet.</p>;
  }

  return (
    <div className="space-y-4">
      {diffs.map((change) => (
        <Card key={change.stmt_id} className="overflow-hidden p-0">
          <div className="grid md:grid-cols-[144px_minmax(0,1fr)]">
            <div className="border-b border-border bg-surface-source p-4 md:border-b-0 md:border-r">
              <CardTitle className="font-mono text-sm">{change.stmt_id}</CardTitle>
              <CardDescription className="mt-2 font-mono text-xs">source span</CardDescription>
            </div>
            <div className="p-4">
              {change.reason && <CardDescription>{change.reason}</CardDescription>}
              <div className="mt-4 grid gap-4 md:grid-cols-2">
            <div>
              <p className="mb-1 text-xs font-bold uppercase text-ink-muted">Original</p>
              <p className="whitespace-pre-wrap rounded-lg bg-surface-muted p-3 text-sm text-ink">
                {change.original || "No original text"}
              </p>
            </div>
            <div>
              <p className="mb-1 text-xs font-bold uppercase text-ink-muted">New</p>
              <p className="whitespace-pre-wrap rounded-lg border border-primary/20 bg-surface-source p-3 text-sm text-ink">
                {change.value || "No new text"}
              </p>
            </div>
              </div>
            </div>
          </div>
        </Card>
      ))}
    </div>
  );
}
