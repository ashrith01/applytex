"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient, type UseMutationResult } from "@tanstack/react-query";
import { ArrowRight, ClipboardCheck, Columns3, ListFilter, Table2 } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth/profile-context";
import type { ApplicationRecord, ApplicationStage, ApplicationStatus, JobPosting } from "@/lib/api/types";
import { ApplicationStatusBadge } from "@/components/shared/application-status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";

const NEXT_STATUSES: Partial<Record<ApplicationStatus, ApplicationStatus[]>> = {
  discovered: ["selected", "skipped"],
  scored: ["selected", "skipped"],
  selected: ["resume_ready", "blocked", "skipped"],
  resume_ready: ["form_scanned", "blocked"],
  form_scanned: ["ready_for_review", "needs_input"],
  needs_input: ["ready_for_review", "skipped"],
  ready_for_review: ["approved", "needs_input", "skipped"],
  approved: ["submitting", "skipped"],
  submitting: ["submitted", "failed"],
};

const STAGES: { id: ApplicationStage; label: string; hint: string }[] = [
  { id: "saved", label: "Saved", hint: "Captured or bookmarked" },
  { id: "selected", label: "Selected", hint: "Worth tailoring" },
  { id: "tailoring", label: "Tailoring", hint: "Resume artifact in progress" },
  { id: "form_review", label: "Form review", hint: "ATS fields scanned" },
  { id: "ready_to_submit", label: "Ready", hint: "Manual submit pending" },
  { id: "submitted", label: "Submitted", hint: "Sent by you" },
  { id: "interview", label: "Interview", hint: "Screen or onsite" },
  { id: "offer", label: "Offer", hint: "Negotiation or decision" },
  { id: "blocked", label: "Blocked", hint: "Needs manual repair" },
];

export default function ApplicationsPage() {
  const qc = useQueryClient();
  const { profileId } = useAuth();
  const [view, setView] = useState<"list" | "board">("list");
  const [filter, setFilter] = useState("");
  const apps = useQuery({
    queryKey: ["applications", profileId],
    queryFn: () => api.listApplications(200, profileId),
    enabled: !!profileId,
  });
  const health = useQuery({
    queryKey: ["applications-health", profileId],
    queryFn: () => api.getApplicationsHealth(profileId),
    enabled: !!profileId,
  });
  const jobs = useQuery({
    queryKey: ["jobs", profileId],
    queryFn: () => api.listJobs(200, profileId),
    enabled: !!profileId,
  });

  const transition = useMutation({
    mutationFn: ({ id, status }: { id: string; status: ApplicationStatus }) =>
      api.transitionApplication(id, status, undefined, profileId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["applications", profileId] });
      qc.invalidateQueries({ queryKey: ["applications-health", profileId] });
    },
  });

  const moveStage = useMutation({
    mutationFn: ({ id, stage }: { id: string; stage: ApplicationStage }) =>
      api.patchApplication(id, { stage }, profileId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["applications", profileId] });
      qc.invalidateQueries({ queryKey: ["applications-health", profileId] });
    },
  });

  const jobMap = useMemo(
    () => new Map((jobs.data ?? []).map((job) => [job.job_id, job])),
    [jobs.data],
  );
  const allApps = apps.data ?? [];
  const filteredApps = allApps.filter((app) => {
    const job = jobMap.get(app.job_id);
    const text = `${job?.title || app.job_title} ${job?.company || app.company} ${app.status} ${app.stage}`.toLowerCase();
    return text.includes(filter.toLowerCase());
  });
  const activeCount = health.data?.active ?? allApps.filter((app) => !["submitted", "skipped", "failed"].includes(app.status)).length;
  const duplicateCleanupCount = health.data?.duplicates_merged ?? 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.14em] text-ink-muted">
            manual submit required
          </p>
          <h1 className="mt-2 text-3xl font-extrabold">Application tracker</h1>
          <p className="mt-2 max-w-3xl text-sm text-ink-muted">
            Track captured jobs from resume tailoring through form review, manual submission, interviews, and follow-up work.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant={view === "list" ? "primary" : "secondary"}
            onClick={() => setView("list")}
          >
            <Table2 className="h-4 w-4" />
            List
          </Button>
          <Button
            variant={view === "board" ? "primary" : "secondary"}
            onClick={() => setView("board")}
          >
            <Columns3 className="h-4 w-4" />
            Board
          </Button>
        </div>
      </div>

      <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        <Metric label="Tracked" value={String(health.data?.total ?? allApps.length)} />
        <Metric label="Active" value={String(activeCount)} />
        <Metric label="Avg current score" value={formatScore(health.data?.average_current_resume_score)} />
        <Metric label="Needs answers" value={String(health.data?.missing_answers ?? allApps.reduce((sum, app) => sum + app.missing_answers_count, 0))} />
        <Metric label="Duplicates cleaned" value={String(duplicateCleanupCount)} />
      </section>

      {duplicateCleanupCount > 0 && (
        <div className="rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-semibold text-emerald-900">
          Duplicate cleanup merged {duplicateCleanupCount} application record{duplicateCleanupCount === 1 ? "" : "s"} into canonical tracker rows.
        </div>
      )}

      <div className="flex items-center gap-2 rounded-card border border-border bg-surface-card px-3 py-2">
        <ListFilter className="h-4 w-4 text-ink-muted" />
        <input
          className="min-w-0 flex-1 bg-transparent text-sm outline-none"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          placeholder="Filter by role, company, status, or stage"
        />
      </div>

      {view === "board" ? (
        <div className="grid gap-4 xl:grid-cols-3 2xl:grid-cols-4">
          {STAGES.map((stage) => {
            const columnApps = filteredApps.filter((app) => app.stage === stage.id);
            return (
              <section key={stage.id} className="min-w-0 rounded-card border border-border bg-surface-muted/50 p-3">
                <div className="mb-3 flex items-start justify-between gap-3">
                  <div>
                    <h2 className="font-bold">{stage.label}</h2>
                    <p className="text-xs text-ink-muted">{stage.hint}</p>
                  </div>
                  <Badge tone="muted">{columnApps.length}</Badge>
                </div>
                <div className="space-y-3">
                  {columnApps.map((app) => (
                    <ApplicationCard
                      key={app.application_id}
                      app={app}
                      job={jobMap.get(app.job_id)}
                      transition={transition}
                      moveStage={moveStage}
                    />
                  ))}
                  {!columnApps.length && (
                    <div className="rounded-md border border-dashed border-border bg-white/70 px-3 py-5 text-center text-xs text-ink-muted">
                      No applications
                    </div>
                  )}
                </div>
              </section>
            );
          })}
        </div>
      ) : (
        <section className="overflow-hidden rounded-md border border-border bg-surface-card">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[980px] table-fixed text-left text-sm">
              <thead className="border-b border-border bg-surface-muted text-xs uppercase text-ink-muted">
                <tr>
                  <th className="w-[16%] px-3 py-3">Company</th>
                  <th className="w-[23%] px-3 py-3">Role</th>
                  <th className="w-[10%] px-3 py-3">Status</th>
                  <th className="w-[8%] px-3 py-3">Stage</th>
                  <th className="w-[7%] px-3 py-3">Current</th>
                  <th className="w-[7%] px-3 py-3">Tailored</th>
                  <th className="w-[7%] px-3 py-3">Artifact</th>
                  <th className="w-[6%] px-3 py-3">Missing</th>
                  <th className="w-[8%] px-3 py-3">Activity</th>
                  <th className="w-[8%] px-3 py-3 text-right">Action</th>
                </tr>
              </thead>
              <tbody>
                {filteredApps.map((app) => {
                  const job = jobMap.get(app.job_id);
                  const missingCount = app.missing_answers_count + app.required_missing.length + app.preferred_missing.length;
                  return (
                    <tr key={app.application_id} className="border-b border-border/80 hover:bg-surface-muted/60 last:border-b-0">
                      <td className="px-3 py-3 font-semibold">
                        <div className="flex items-center gap-3">
                          <span className="h-8 w-1 rounded-full bg-primary" aria-hidden="true" />
                          <span className="min-w-0 truncate">{job?.company || app.company || "Unknown company"}</span>
                        </div>
                      </td>
                      <td className="px-3 py-3">
                        <div className="truncate font-bold" title={job?.title || app.job_title || "Saved application"}>
                          {job?.title || app.job_title || "Saved application"}
                        </div>
                        <div className="mt-1 text-xs text-ink-muted">
                          {app.location || job?.location || "Location not listed"}
                        </div>
                      </td>
                      <td className="px-3 py-3"><ApplicationStatusBadge status={app.status} /></td>
                      <td className="px-3 py-3">{app.stage.replace(/_/g, " ")}</td>
                      <td className="px-3 py-3 font-mono font-semibold">{formatScore(app.current_resume_score)}</td>
                      <td className="px-3 py-3 font-mono font-semibold">{formatScore(app.tailored_resume_score)}</td>
                      <td className="px-3 py-3">
                        <Badge tone={app.latest_resume_artifact_id ? "success" : "muted"}>
                          {app.latest_resume_artifact_id ? "Resume" : "None"}
                        </Badge>
                      </td>
                      <td className="px-3 py-3">
                        {missingCount > 0 ? (
                          <Badge tone="warning">{missingCount}</Badge>
                        ) : (
                          <span className="text-ink-muted">0</span>
                        )}
                      </td>
                      <td className="px-3 py-3 text-xs text-ink-muted">{formatDate(app.last_activity_at || app.updated_at)}</td>
                      <td className="px-3 py-3 text-right">
                        <Link href={`/applications/${encodeURIComponent(app.application_id)}`}>
                          <Button size="sm" variant="secondary">Open</Button>
                        </Link>
                      </td>
                    </tr>
                  );
                })}
                {!filteredApps.length && (
                  <tr>
                    <td className="px-4 py-10 text-center text-sm text-ink-muted" colSpan={10}>
                      No applications match the current filter.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {!allApps.length && (
        <Card className="p-5 text-sm text-ink-muted">
          No applications yet. Capturing a job from the extension will create the first tracked review.
        </Card>
      )}
    </div>
  );
}

function ApplicationCard({
  app,
  job,
  transition,
  moveStage,
}: {
  app: ApplicationRecord;
  job?: JobPosting;
  transition: UseMutationResult<ApplicationRecord, Error, { id: string; status: ApplicationStatus }>;
  moveStage: UseMutationResult<ApplicationRecord, Error, { id: string; stage: ApplicationStage }>;
}) {
  const next = NEXT_STATUSES[app.status] ?? [];
  const title = job?.title || app.job_title || "Saved application";
  const company = job?.company || app.company || "Unknown company";
  return (
    <Card className="relative overflow-hidden p-4 pl-5">
      <div className="absolute inset-y-0 left-0 w-1 bg-accent" aria-hidden="true" />
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex items-center gap-2 font-mono text-xs uppercase tracking-[0.12em] text-ink-muted">
            <ClipboardCheck className="h-3.5 w-3.5" />
            {company}
          </p>
          <CardTitle className="mt-1 break-words text-base">{title}</CardTitle>
          <CardDescription className="mt-1">
            {app.current_resume_score != null ? `${formatScore(app.current_resume_score)} current score` : statusHint(app.status)}
          </CardDescription>
        </div>
        <ApplicationStatusBadge status={app.status} />
      </div>

      <div className="mt-3 flex flex-wrap gap-2 text-xs">
        <Badge tone={app.latest_resume_artifact_id ? "success" : "muted"}>
          {app.latest_resume_artifact_id ? "resume ready" : "no artifact"}
        </Badge>
        {app.tailored_resume_score != null && <Badge tone="success">{formatScore(app.tailored_resume_score)} tailored</Badge>}
        {app.missing_answers_count > 0 && <Badge tone="warning">{app.missing_answers_count} fields</Badge>}
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <Link href={`/applications/${encodeURIComponent(app.application_id)}`}>
          <Button size="sm" variant="secondary">Open</Button>
        </Link>
        {job && (
          <Link href={`/tailor/${encodeURIComponent(job.job_id)}?application_id=${app.application_id}`}>
            <Button size="sm">
              Tailor
              <ArrowRight className="h-4 w-4" />
            </Button>
          </Link>
        )}
      </div>

      <details className="mt-3 rounded-md border border-border bg-surface-muted p-3">
        <summary className="cursor-pointer text-xs font-bold uppercase tracking-[0.12em] text-ink-muted">
          Update
        </summary>
        <div className="mt-3 space-y-3">
          <label className="block text-xs font-bold uppercase tracking-wide text-ink-muted">
            Stage
            <select
              className="mt-1 w-full rounded-md border border-border bg-white px-2 py-2 text-sm normal-case tracking-normal text-ink"
              value={app.stage}
              disabled={moveStage.isPending}
              onChange={(event) =>
                moveStage.mutate({
                  id: app.application_id,
                  stage: event.target.value as ApplicationStage,
                })
              }
            >
              {STAGES.map((stage) => (
                <option key={stage.id} value={stage.id}>{stage.label}</option>
              ))}
            </select>
          </label>
          {next.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {next.map((status) => (
                <Button
                  key={status}
                  size="sm"
                  variant="secondary"
                  disabled={transition.isPending}
                  onClick={() => transition.mutate({ id: app.application_id, status })}
                >
                  Set {status.replace(/_/g, " ")}
                </Button>
              ))}
            </div>
          )}
        </div>
      </details>
    </Card>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <Card className="relative overflow-hidden p-4 pl-5">
      <div className="absolute inset-y-0 left-0 w-1 bg-primary" aria-hidden="true" />
      <p className="font-mono text-xs uppercase tracking-[0.12em] text-ink-muted">{label}</p>
      <p className="mt-1 text-3xl font-black">{value}</p>
    </Card>
  );
}

function formatScore(score: number | null | undefined): string {
  return score == null ? "-" : score.toFixed(1);
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "Not yet";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function statusHint(status: ApplicationStatus): string {
  if (status === "needs_input") return "Answers needed";
  if (status === "ready_for_review") return "Review form";
  if (status === "resume_ready") return "Resume approved";
  if (status === "form_scanned") return "Form scanned";
  if (status === "submitted") return "Submitted";
  if (status === "skipped") return "Skipped";
  if (status === "failed" || status === "blocked") return "Needs attention";
  return "Continue workflow";
}
