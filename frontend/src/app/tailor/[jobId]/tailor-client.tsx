"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { use, useEffect, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ExternalLink,
  FileText,
  Github,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  ApplicationArtifact,
  ProjectRankResponse,
  ProjectRecommendation,
  TailorSessionResponse,
} from "@/lib/api/types";
import { FitScoreHero } from "@/components/shared/fit-score-hero";
import { SkillConfirmGrid } from "@/components/shared/skill-confirm-grid";
import { Stepper } from "@/components/shared/stepper";
import { StatementDiffList } from "@/components/shared/statement-diff-list";
import { PdfPane } from "@/components/shared/pdf-pane";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/lib/auth/profile-context";

const STEPS = ["Analyze", "Select projects", "Confirm evidence", "Review", "Approve"];

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
  const returnTarget = searchParams.get("return");
  const { profileId } = useAuth();
  const qc = useQueryClient();
  const [step, setStep] = useState(1);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [confirmedSkills, setConfirmedSkills] = useState<string[]>([]);
  const [instruction, setInstruction] = useState("");
  const [bootstrapped, setBootstrapped] = useState(false);
  const [approvedArtifact, setApprovedArtifact] = useState<ApplicationArtifact | null>(null);
  const [selectedProjectIds, setSelectedProjectIds] = useState<string[]>([]);

  const mergeProjectRank = (rank: ProjectRankResponse) => {
    qc.setQueryData<TailorSessionResponse | undefined>(["tailor", sessionId], (old) =>
      old
        ? {
            ...old,
            project_recommendations: rank.project_recommendations,
            selected_project_ids: rank.selected_project_ids,
            project_filter_warnings: rank.project_filter_warnings,
          }
        : old,
    );
    setSelectedProjectIds(rank.selected_project_ids);
  };

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
      setSelectedProjectIds(data.selected_project_ids);
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

  const data = session.data as TailorSessionResponse | undefined;
  const selectedProjectIdsKey = data?.selected_project_ids.join("|") ?? "";
  const dataSessionId = data?.session_id ?? "";

  useEffect(() => {
    if (dataSessionId) {
      setSelectedProjectIds(selectedProjectIdsKey ? selectedProjectIdsKey.split("|") : []);
    }
  }, [dataSessionId, selectedProjectIdsKey]);

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
      setStep(4);
    },
  });

  const syncProjects = useMutation({
    mutationFn: async () => {
      await api.syncGithubProjects(profileId);
      return api.rankTailorProjects(sessionId!);
    },
    onSuccess: mergeProjectRank,
  });

  const updateProjects = useMutation({
    mutationFn: (ids: string[]) => api.updateTailorProjects(sessionId!, ids),
    onSuccess: mergeProjectRank,
  });

  const refine = useMutation({
    mutationFn: () => api.refineTailorSession(sessionId!, { instruction }),
    onSuccess: (data) => {
      qc.setQueryData(["tailor", sessionId], data);
      setInstruction("");
    },
  });

  const approve = useMutation({
    mutationFn: () =>
      api.approveTailorSession(sessionId!, {
        application_id: applicationId,
      }),
    onSuccess: (artifact) => {
      setApprovedArtifact(artifact);
      setStep(5);
      if (applicationId) {
        qc.invalidateQueries({ queryKey: ["application", applicationId] });
      }
      qc.invalidateQueries({ queryKey: ["applications"] });
    },
  });

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
  const matchBreakdown = [
    {
      label: "Required qualifications",
      score: data.match_preview.baseline_ats.required_score ?? 0,
      detail: `${data.match_preview.baseline_ats.required_found?.length ?? 0} matched`,
    },
    {
      label: "Preferred qualifications",
      score: data.match_preview.baseline_ats.preferred_score ?? 0,
      detail: `${data.match_preview.baseline_ats.preferred_found?.length ?? 0} matched`,
    },
    {
      label: "Job-description keywords",
      score: data.match_preview.baseline_ats.keyword_score ?? 0,
      detail: `${data.match_preview.baseline_ats.keyword_hits?.length ?? 0} found`,
    },
  ];
  const lastResult = data.last_result as TailorLastResult | null;
  const pdfB64 = lastResult?.modified_pdf_b64 ?? null;
  const pageCount = lastResult?.page_count;
  const blocksApproval = Boolean(lastResult?.overflow || lastResult?.visual_overflow || (pageCount && pageCount > 1));
  const projectRecommendations = data.project_recommendations ?? [];
  const selectedProjectSet = new Set(selectedProjectIds);
  const selectedResumeProjects = projectRecommendations.filter(
    (item) => item.project.source === "resume" && selectedProjectSet.has(item.project.project_id),
  );
  const resumeProjects = projectRecommendations.filter((item) => item.project.source === "resume");
  const githubProjects = projectRecommendations.filter((item) => item.project.source === "github");
  const toggleProject = (projectId: string) => {
    const next = selectedProjectSet.has(projectId)
      ? selectedProjectIds.filter((item) => item !== projectId)
      : [...selectedProjectIds, projectId];
    setSelectedProjectIds(next);
    updateProjects.mutate(next);
  };

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.14em] text-ink-muted">local tailoring studio</p>
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
            <CardTitle>Why this score</CardTitle>
            <CardDescription className="mt-2">
              ApplyTeX separates must-have qualifications, preferred evidence, and broader job-description language.
            </CardDescription>
            <div className="mt-4 grid gap-3 md:grid-cols-3">
              {matchBreakdown.map((item) => (
                <div key={item.label} className="rounded-xl border border-border bg-surface-muted/40 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <p className="text-sm font-bold text-ink">{item.label}</p>
                    <span className="font-mono text-sm font-black text-primary">{item.score.toFixed(0)}%</span>
                  </div>
                  <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-border/70">
                    <div className="h-full rounded-full bg-primary" style={{ width: `${Math.max(0, Math.min(100, item.score))}%` }} />
                  </div>
                  <p className="mt-2 text-xs text-ink-muted">{item.detail}</p>
                </div>
              ))}
            </div>
            <div className="mt-4 rounded-xl border border-budget/20 bg-budget-soft px-4 py-3">
              <p className="text-sm font-bold text-ink">Missing required evidence</p>
              <p className="mt-1 text-sm text-ink-muted">
                {data.match_preview.baseline_ats.required_missing?.join(", ") || "No required gaps detected."}
              </p>
            </div>
          </Card>
          <div className="flex justify-center">
            <Button size="lg" onClick={() => setStep(2)}>
              Select project evidence
              <ArrowRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}

      {step === 2 && (
        <div className="space-y-6">
          <Card className="border-primary/20 bg-surface-source">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <CardTitle>Select projects for this resume</CardTitle>
                <CardDescription className="mt-2 max-w-3xl">
                  Resume-backed projects can be kept or removed from this tailored PDF. GitHub projects are recommendation evidence in v1 and are not inserted into the resume.
                </CardDescription>
              </div>
              <Button
                variant="secondary"
                onClick={() => syncProjects.mutate()}
                disabled={syncProjects.isPending || !sessionId}
              >
                <RefreshCw className={`h-4 w-4 ${syncProjects.isPending ? "animate-spin" : ""}`} />
                Sync public GitHub
              </Button>
            </div>
            {syncProjects.isError && (
              <p className="mt-4 rounded-md border border-budget/20 bg-budget-soft px-3 py-2 text-sm text-budget">
                {(syncProjects.error as Error).message}
              </p>
            )}
            {data.project_filter_warnings.length > 0 && (
              <p className="mt-4 rounded-md border border-budget/20 bg-budget-soft px-3 py-2 text-sm text-budget">
                {data.project_filter_warnings[0]}
              </p>
            )}
          </Card>

          <section className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-lg font-extrabold">Selected for resume</h2>
              <span className="font-mono text-xs font-bold text-ink-muted">{selectedResumeProjects.length} selected</span>
            </div>
            {selectedResumeProjects.length > 0 ? (
              <div className="grid gap-3 xl:grid-cols-2">
                {selectedResumeProjects.map((item) => (
                  <ProjectEvidenceCard
                    key={item.project.project_id}
                    recommendation={item}
                    selected
                    onToggle={() => toggleProject(item.project.project_id)}
                  />
                ))}
              </div>
            ) : (
              <Card className="border-budget/20 bg-budget-soft">
                <CardDescription>Select at least one resume project if the tailored PDF should keep project evidence.</CardDescription>
              </Card>
            )}
          </section>

          <section className="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
            <div className="space-y-3">
              <h2 className="text-lg font-extrabold">Resume projects</h2>
              <div className="grid gap-3">
                {resumeProjects.map((item) => (
                  <ProjectEvidenceCard
                    key={item.project.project_id}
                    recommendation={item}
                    selected={selectedProjectSet.has(item.project.project_id)}
                    onToggle={() => toggleProject(item.project.project_id)}
                  />
                ))}
                {resumeProjects.length === 0 && (
                  <Card>
                    <CardDescription>No removable resume projects were found in the current LaTeX source.</CardDescription>
                  </Card>
                )}
              </div>
            </div>
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <Github className="h-5 w-5 text-primary" />
                <h2 className="text-lg font-extrabold">GitHub evidence</h2>
              </div>
              <div className="grid gap-3">
                {githubProjects.map((item) => (
                  <ProjectEvidenceCard
                    key={item.project.project_id}
                    recommendation={item}
                    selected={false}
                    evidenceOnly
                  />
                ))}
                {githubProjects.length === 0 && (
                  <Card>
                    <CardDescription>Sync public GitHub projects to see supporting evidence and project ideas for later resume updates.</CardDescription>
                  </Card>
                )}
              </div>
            </div>
          </section>

          <div className="flex justify-center">
            <Button size="lg" onClick={() => setStep(3)}>
              Confirm defensible skills
              <ArrowRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}

      {step === 3 && (
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

      {(step === 4 || step === 5) && (
        <div className="space-y-6">
          <Card className={`relative overflow-hidden pl-7 ${blocksApproval ? "border-budget/30 bg-budget-soft" : "border-primary/20 bg-surface-source"}`}>
            <div className={`absolute inset-y-0 left-0 w-1.5 ${blocksApproval ? "bg-budget" : "bg-primary"}`} aria-hidden="true" />
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
          <Card className="relative overflow-hidden border-accent/20 bg-white pl-7">
            <div className="absolute inset-y-0 left-0 w-1.5 bg-accent" aria-hidden="true" />
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <CardTitle className="flex items-center gap-2">
                  <CheckCircle2 className="h-5 w-5 text-accent" />
                  Application artifact
                </CardTitle>
                <CardDescription className="mt-2">
                  Approve the reviewed PDF before returning to the extension. The extension will prefer this artifact for resume upload.
                </CardDescription>
              </div>
              <Button
                onClick={() => approve.mutate()}
                disabled={approve.isPending || blocksApproval || !applicationId}
              >
                {approve.isPending ? "Approving..." : "Approve for application"}
              </Button>
            </div>
            {!applicationId && (
              <p className="mt-3 rounded-md border border-budget/20 bg-budget-soft px-3 py-2 text-sm text-budget">
                This tailor session is not linked to an application. Open it from the extension or application tracker to approve an upload artifact.
              </p>
            )}
            {approvedArtifact && (
              <div className="mt-4 rounded-md border border-primary/20 bg-surface-source px-4 py-3 text-sm">
                <p className="font-bold text-ink">Approved artifact ready</p>
                <p className="mt-1 text-ink-muted">
                  {approvedArtifact.filename} is saved for this application.
                  {returnTarget === "extension" ? " Return to the extension and upload the approved resume." : ""}
                </p>
              </div>
            )}
            {approve.isError && (
              <p className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                {(approve.error as Error).message}
              </p>
            )}
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

function ProjectEvidenceCard({
  recommendation,
  selected,
  onToggle,
  evidenceOnly = false,
}: {
  recommendation: ProjectRecommendation;
  selected: boolean;
  onToggle?: () => void;
  evidenceOnly?: boolean;
}) {
  const project = recommendation.project;
  const score = Math.round(recommendation.fit_score);
  const terms = recommendation.matched_terms.slice(0, 6);
  return (
    <Card className={`relative overflow-hidden pl-7 ${selected ? "border-primary/30 bg-surface-source" : "bg-white"}`}>
      <div className={`absolute inset-y-0 left-0 w-1.5 ${selected ? "bg-primary" : evidenceOnly ? "bg-accent" : "bg-border"}`} aria-hidden="true" />
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-md border border-border bg-surface-muted px-2 py-1 font-mono text-[11px] font-black uppercase text-ink-muted">
              {project.source === "github" ? "GitHub" : "Resume"}
            </span>
            {evidenceOnly && (
              <span className="rounded-md border border-budget/20 bg-budget-soft px-2 py-1 font-mono text-[11px] font-black uppercase text-budget">
                Evidence only
              </span>
            )}
            {project.credibility_score != null && (
              <span className="rounded-md border border-primary/20 bg-surface-source px-2 py-1 font-mono text-[11px] font-black uppercase text-primary">
                Evidence {project.credibility_score.toFixed(0)}
              </span>
            )}
          </div>
          <CardTitle className="mt-3 text-lg">{project.title}</CardTitle>
          <CardDescription className="mt-2">{recommendation.rationale}</CardDescription>
        </div>
        <div className="flex shrink-0 items-start gap-3">
          <div className="rounded-md border border-border bg-surface-card px-3 py-2 text-right">
            <div className="font-mono text-[10px] font-black uppercase tracking-[0.12em] text-ink-muted">fit</div>
            <div className="text-xl font-black text-primary">{score}%</div>
          </div>
          {!evidenceOnly && (
            <label className="flex h-10 w-10 cursor-pointer items-center justify-center rounded-md border border-border bg-white">
              <input
                aria-label={`Select ${project.title}`}
                checked={selected}
                className="h-4 w-4 accent-emerald-600"
                type="checkbox"
                onChange={onToggle}
              />
            </label>
          )}
        </div>
      </div>

      {recommendation.summary_points.length > 0 && (
        <ul className="mt-4 space-y-2 text-sm text-ink">
          {recommendation.summary_points.slice(0, 2).map((point) => (
            <li key={point} className="flex gap-2">
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
              <span>{point}</span>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        {terms.map((term) => (
          <span key={term} className="rounded-md bg-surface-muted px-2 py-1 text-xs font-bold text-ink">
            {term}
          </span>
        ))}
        {terms.length === 0 && (
          <span className="rounded-md bg-surface-muted px-2 py-1 text-xs font-bold text-ink-muted">
            Limited JD overlap
          </span>
        )}
      </div>

      {(project.languages.length > 0 || project.topics.length > 0 || project.url) && (
        <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-ink-muted">
          {[...project.languages, ...project.topics].slice(0, 5).map((item) => (
            <span key={item} className="font-mono font-bold">
              {item}
            </span>
          ))}
          {project.url && (
            <a
              className="inline-flex items-center gap-1 font-bold text-primary hover:underline"
              href={project.url}
              rel="noreferrer"
              target="_blank"
            >
              Open link
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          )}
        </div>
      )}
    </Card>
  );
}
