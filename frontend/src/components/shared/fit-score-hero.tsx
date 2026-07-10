import { cn, scoreLabel } from "@/lib/utils";

interface FitScoreHeroProps {
  score: number;
  title?: string;
  subtitle?: string;
}

export function FitScoreHero({ score, title, subtitle }: FitScoreHeroProps) {
  return (
    <div className="grid gap-6 rounded-card border border-border bg-surface-card p-6 md:grid-cols-[1fr_auto] md:p-8">
      <div>
        <p className="font-mono text-xs font-bold uppercase tracking-[0.14em] text-ink-muted">evidence match</p>
        <h1 className="mt-2 font-serif text-3xl font-black leading-tight text-ink md:text-4xl">
          {title ?? `${scoreLabel(score)} match for this role`}
        </h1>
        {subtitle && <p className="mt-3 max-w-2xl text-ink-muted">{subtitle}</p>}
        <div className="mt-5 grid max-w-2xl gap-2 font-mono text-xs text-ink-muted sm:grid-cols-3">
          <span className="rounded-md border border-border bg-surface-source px-3 py-2">editable: summary / work / projects / skills</span>
          <span className="rounded-md border border-border bg-surface-source px-3 py-2">locked: education / publications</span>
          <span className="rounded-md border border-border bg-surface-source px-3 py-2">gate: one-page PDF</span>
        </div>
      </div>
      <div className="flex flex-col items-center justify-center rounded-card border border-primary/20 bg-surface-source px-8 py-6 text-center">
        <div className="text-5xl font-black text-ink">{(score / 10).toFixed(1)}</div>
        <div className="mt-1 font-bold text-ink">{scoreLabel(score)}</div>
        <div className="mt-1 text-xs text-ink-muted">Submission fit / 10</div>
      </div>
    </div>
  );
}
