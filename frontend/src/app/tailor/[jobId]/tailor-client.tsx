"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { use, useEffect, useState } from "react";
import { AlertTriangle, ArrowRight, FileText, ShieldCheck } from "lucide-react";
import { api } from "@/lib/api";
import type { TailorSessionResponse } from "@/lib/api/types";
import { FitScoreHero } from "@/components/shared/fit-score-hero";
import { SkillConfirmGrid } from "@/components/shared/skill-confirm-grid";
import { Stepper } from "@/components/shared/stepper";
import { StatementDiffList } from "@/components/shared/statement-diff-list";
import { PdfPane } from "@/components/shared/pdf-pane";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/lib/auth/profile-context";

const STEPS = ["Check fit", "Confirm evidence", "Review source diff"];

type TailorLastResult = {
  modified_pdf_b64?: string | null;
  modified_latex?: string | null;
  ats_after?: { score?: number };
  overflow?: boolean;
  visual_overflow?: boolean;
  page_count?: number;
  warnings?: string[];
};

export default function TailorPageClient({ params }: { params: Promise<{ jobId: string }> }) {
  const { jobId } = use(params);
  const searchParams = useSearchParams();
  const applicationId = searchParams.get("application_id");
  const { profileId } = useAuth();
  const qc = useQueryClient();
  const [step, setStep] = useState(1);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [confirmedSkills, setConfirmedSkills] = useState<string[]>([]);
  const [instruction, setInstruction] = useState("");
  const [bootstrapped, setBootstrapped] = useState(false);

  const bootstrap = useMutation({
    mutationFn: () =>
      api.createTailorSession({
        job_id: jobId,
        profile_id: profileId!,
        application_id: applicationId ?? undefined,
      }),
    onSuccess: (data) => {
      setSessionId(data.session_id);
      setConfirmedSkills(data.confirmed_skills);
    },
  });

  useEffect(() => {
    if (profileId && !bootstrapped) {
      setBootstrapped(true);
      bootstrap.mutate();
    }
    // bootstrap.mutate is stable enough for one-shot session creation
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileId, bootstrapped]);

  const session = useQuery({
    queryKey: ["tailor", sessionId],
    queryFn: () => api.getTailorSession(sessionId!),
    enabled: !!sessionId,
  });

  const updateSkills = useMutation({
    mutationFn: (skills: string[]) =>
      api.updateTailorSession(sessionId!, { confirmed_skills: skills }),
    onSuccess: (data) => {
      qc.setQueryData(["tailor", sessionId], data);
      setConfirmedSkills(data.confirmed_skills);
    },
  });

  const optimize = useMutation({
    mutationFn: () => api.optimizeTailorSession(sessionId!),
    onSuccess: (data) => {
      qc.setQueryData(["tailor", sessionId], data);
      setStep(3);
    },
  });

  const refine = useMutation({
    mutationFn: () => api.refineTailorSession(sessionId!, { instruction }),
    onSuccess: (data) => {
      qc.setQueryData(["tailor", sessionId], data);
      setInstruction("");
    },
  });

  const data = session.data as TailorSessionResponse | undefined;

  if (bootstrap.isError) {
    return (
      <Card className="border-red-200 bg-red-50">
        <CardTitle>Could not start tailor flow</CardTitle>
        <CardDescription>{(bootstrap.error as Error).message}</CardDescription>
      </Card>
    );
  }

  if (!data) {
    return <p className="text-ink-muted">Preparing tailor session...</p>;
  }

  const score = data.match_preview.baseline_ats.score ?? 0;
  const lastResult = data.last_result as TailorLastResult | null;
  const pdfB64 = lastResult?.modified_pdf_b64 ?? null;
  const pageCount = lastResult?.page_count;
  const blocksApproval = Boolean(lastResult?.overflow || lastResult?.visual_overflow || (pageCount && pageCount > 1));

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.14em] text-ink-muted">source-preserving rewrite</p>
          <h1 className="mt-2 font-serif text-4xl font-black leading-tight">Tailor source for this job</h1>
          <p className="mt-2 text-ink-muted">{data.job.company} / {data.job.title}</p>
        </div>
        <div className="flex gap-2">
          <span className="rounded-md border border-border bg-surface-card px-3 py-1 font-mono text-xs font-bold">local preview</span>
          <span className="rounded-md border border-primary/20 bg-surface-source px-3 py-1 font-mono text-xs font-bold text-primary">one-page enforced</span>
        </div>
      </div>

      <Stepper steps={STEPS} current={step} />

      {step === 1 && (
        <div className="space-y-6">
          <FitScoreHero
            score={score}
            title="Current resume before edits"
            subtitle={`The current source scores ${score.toFixed(1)}/100 against this job. The rewrite can only use confirmed evidence and editable LaTeX statement spans.`}
          />
          <Card>
            <CardTitle>Keyword gaps</CardTitle>
            <CardDescription className="mt-2">
              Found: {data.match_preview.baseline_ats.required_found?.length ?? 0} required / Missing:{" "}
              {data.match_preview.baseline_ats.required_missing?.join(", ") || "none"}
            </CardDescription>
          </Card>
          <div className="flex justify-center">
            <Button size="lg" onClick={() => setStep(2)}>
              Confirm defensible skills
              <ArrowRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}

      {step === 2 && (
        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <CardTitle>Confirm evidence</CardTitle>
            <CardDescription className="mt-2">
              Select only skills you can truthfully defend in an interview. Unconfirmed skills stay out of generated bullets.
            </CardDescription>
            <div className="mt-4">
              <SkillConfirmGrid
                groups={data.match_preview.skill_groups}
                selected={confirmedSkills}
                onChange={(skills) => {
                  setConfirmedSkills(skills);
                  updateSkills.mutate(skills);
                }}
              />
            </div>
          </Card>
          <Card className="bg-surface-source">
            <CardTitle>Run source rewrite</CardTitle>
            <CardDescription className="mt-2">
              Rewrites summary, work, projects, and skills. Locked sections and LaTeX structure remain untouched.
            </CardDescription>
            <Button className="mt-6 w-full" onClick={() => optimize.mutate()} disabled={optimize.isPending}>
              {optimize.isPending ? "Rewriting..." : "Rewrite editable statements"}
            </Button>
            {optimize.isError && (
              <p className="mt-2 text-sm text-red-600">{(optimize.error as Error).message}</p>
            )}
          </Card>
        </div>
      )}

      {step === 3 && (
        <div className="space-y-6">
          <Card className={blocksApproval ? "border-budget/30 bg-budget-soft" : "border-primary/20 bg-surface-source"}>
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <CardTitle className="flex items-center gap-2">
                  {blocksApproval ? <AlertTriangle className="h-5 w-5 text-budget" /> : <ShieldCheck className="h-5 w-5 text-primary" />}
                  {blocksApproval ? "One-page gate needs attention" : "One-page gate passed"}
                </CardTitle>
                <CardDescription className="mt-2">
                  {pageCount ? `${pageCount} page${pageCount === 1 ? "" : "s"} rendered.` : "PDF render will appear when available."}
                  {lastResult?.warnings?.length ? ` ${lastResult.warnings[0]}` : ""}
                </CardDescription>
              </div>
              {lastResult?.ats_after?.score != null && (
                <div className="rounded-md border border-border bg-white px-4 py-3 text-right">
                  <div className="font-mono text-xs uppercase tracking-[0.12em] text-ink-muted">fit after</div>
                  <div className="text-2xl font-black">{lastResult.ats_after.score.toFixed(1)}</div>
                </div>
              )}
            </div>
          </Card>
          <div className="grid gap-6 xl:grid-cols-2">
            <PdfPane base64={pdfB64} />
            <Card>
              <CardTitle>Refine wording</CardTitle>
              <CardDescription className="mt-2">
                Add a precise instruction for the current tailored source. Keep changes factual and defensible.
              </CardDescription>
              <textarea
                className="mt-4 min-h-[120px] w-full rounded-md border border-border p-3 text-sm"
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                placeholder="Example: make the project bullets more backend-focused without adding new tools"
              />
              <Button className="mt-3 w-full" onClick={() => refine.mutate()} disabled={!instruction.trim() || refine.isPending}>
                {refine.isPending ? "Applying..." : "Apply instruction"}
              </Button>
              {data.last_result && (
                <p className="mt-4 text-sm text-ink-muted">
                  Fit after changes: {lastResult?.ats_after?.score?.toFixed(1) ?? "not scored"}/100
                </p>
              )}
            </Card>
          </div>
          <div>
            <div className="mb-3 flex items-center gap-2">
              <FileText className="h-5 w-5 text-primary" />
              <h2 className="text-2xl font-extrabold">Changed statements</h2>
            </div>
            <StatementDiffList diffs={data.diff} />
          </div>
          {lastResult?.modified_latex ? (
            <a
              href={`data:text/plain;charset=utf-8,${encodeURIComponent(String(lastResult.modified_latex))}`}
              download="optimized_resume.tex"
              className="inline-flex"
            >
              <Button variant="secondary">
                <FileText className="h-4 w-4" />
                Download tailored .tex
              </Button>
            </a>
          ) : null}
        </div>
      )}
    </div>
  );
}
