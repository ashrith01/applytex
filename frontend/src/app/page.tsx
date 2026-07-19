"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, BriefcaseBusiness, CheckCircle2, ClipboardCheck, FileText, ShieldCheck } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth/profile-context";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ApplicationStatusBadge } from "@/components/shared/application-status-badge";
import type { ApplicationRecord, ApplicationStatus, JobPosting } from "@/lib/api/types";

const REVIEW_STATUSES: ApplicationStatus[] = ["selected", "resume_ready", "form_scanned", "needs_input", "ready_for_review"];

export default function DashboardPage() {
  const { profileId, activeProfile } = useAuth();
  const jobs = useQuery({
    queryKey: ["jobs", profileId],
    queryFn: () => api.listJobs(8, profileId),
    enabled: !!profileId,
  });
  const applications = useQuery({
    queryKey: ["applications", profileId],
    queryFn: () => api.listApplications(8, profileId),
    enabled: !!profileId,
  });
  const health = useQuery({
    queryKey: ["applications-health", profileId],
    queryFn: () => api.getApplicationsHealth(profileId),
    enabled: !!profileId,
  });
  const setup = useQuery({
    queryKey: ["profile-setup", profileId],
    queryFn: () => api.getProfileSetup(profileId!),
    enabled: !!profileId,
  });

  const recentJobs = jobs.data ?? [];
  const recentApps = applications.data ?? [];
  const activeApps = recentApps.filter((app) => REVIEW_STATUSES.includes(app.status));
  const jobMap = new Map(recentJobs.map((job) => [job.job_id, job]));
  const resumeReady = Boolean(activeProfile?.has_latex_source);
  const answersReady = Boolean(setup.data?.ready_for_basic_autofill);
  const currentJob = recentJobs[0];
  const next = nextAction({ resumeReady, answersReady, currentJob });
  const needsAnswers = health.data?.missing_answers ?? recentApps.reduce((sum, app) => sum + app.missing_answers_count, 0);

  return (
    <div className="space-y-8">
      <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        <DashboardMetric label="Active applications" value={health.data?.active ?? activeApps.length} detail="Need resume or form review" />
        <DashboardMetric label="Captured jobs" value={health.data?.captured_jobs ?? recentJobs.length} detail="Latest local job evidence" />
        <DashboardMetric label="Avg current score" value={formatScore(health.data?.average_current_resume_score)} detail="Fast JD fit snapshot" />
        <DashboardMetric label="Missing answers" value={needsAnswers} detail="Resolve before autofill" />
        <DashboardMetric label="Duplicates cleaned" value={health.data?.duplicates_merged ?? 0} detail="Merged into canonical rows" />
      </section>

      <section className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="relative overflow-hidden rounded-card border border-border bg-surface-card p-6 md:p-8">
          <div className="absolute inset-y-0 left-0 w-2 bg-primary" aria-hidden="true" />
          <p className="font-mono text-xs uppercase tracking-[0.16em] text-ink-muted">
            stmt_index / review gate
          </p>
          <h1 className="mt-3 max-w-3xl font-serif text-4xl font-black leading-tight text-ink md:text-5xl">
            Tailor the next application without touching the resume layout.
          </h1>
          <p className="mt-4 max-w-2xl text-base leading-7 text-ink-muted">
            Capture a job, confirm only defensible skills, review each changed statement, then upload a one-page PDF from the browser extension.
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <Link href={next.href}>
              <Button size="lg">
                {next.label}
                <ArrowRight className="h-4 w-4" />
              </Button>
            </Link>
            <Link href="/profile/resume">
              <Button size="lg" variant="secondary">
                <FileText className="h-4 w-4" />
                Resume source
              </Button>
            </Link>
          </div>
        </div>

        <Card className="bg-surface-source">
          <CardTitle>Readiness</CardTitle>
          <CardDescription className="mt-2">
            The extension should only fill or upload after these checks are clear.
          </CardDescription>
          <div className="mt-5 space-y-4">
            <ReadinessRow
              ready={resumeReady}
              title="LaTeX source"
              detail={resumeReady ? "Formatting can be preserved." : "Upload .tex before tailoring."}
            />
            <ReadinessRow
              ready={answersReady}
              title="Application answers"
              detail={answersReady ? "Basic autofill fields are ready." : `${setup.data?.missing_required.length ?? "Several"} required answers missing.`}
            />
            <ReadinessRow
              ready={Boolean(currentJob)}
              title="Captured job"
              detail={currentJob ? `${currentJob.company} / ${currentJob.title}` : "Capture a job from the extension or open a saved job."}
            />
          </div>
        </Card>
      </section>

      <section className="grid gap-6 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)]">
        <div>
          <div className="mb-4 flex items-center justify-between gap-4">
            <div>
              <h2 className="text-2xl font-extrabold">Captured jobs</h2>
              <p className="mt-1 text-sm text-ink-muted">Use the latest posting as the source of truth for resume edits.</p>
            </div>
            <Link href="/jobs" className="text-sm font-bold text-ink-muted hover:text-ink">
              View jobs
            </Link>
          </div>
          <div className="space-y-3">
            {recentJobs.slice(0, 4).map((job) => (
              <JobRow key={job.job_id} job={job} />
            ))}
            {!recentJobs.length && (
              <Card className="p-4 text-sm text-ink-muted">
                No jobs captured yet. Open a supported job page and use the extension panel.
              </Card>
            )}
          </div>
        </div>

        <div>
          <div className="mb-4 flex items-center justify-between gap-4">
            <div>
              <h2 className="text-2xl font-extrabold">Review queue</h2>
              <p className="mt-1 text-sm text-ink-muted">Applications that still need a resume or form review.</p>
            </div>
            <Link href="/applications" className="text-sm font-bold text-ink-muted hover:text-ink">
              View all
            </Link>
          </div>
          <div className="space-y-3">
            {(activeApps.length ? activeApps : recentApps).slice(0, 5).map((app) => (
              <ApplicationRow key={app.application_id} app={app} job={jobMap.get(app.job_id)} />
            ))}
            {!recentApps.length && (
              <Card className="p-4 text-sm text-ink-muted">
                No applications tracked yet. Capturing a job from the extension will start one.
              </Card>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}

function DashboardMetric({ label, value, detail }: { label: string; value: number | string; detail: string }) {
  return (
    <Card className="relative overflow-hidden p-4 pl-5">
      <div className="absolute inset-y-0 left-0 w-1 bg-primary" aria-hidden="true" />
      <p className="font-mono text-xs uppercase tracking-[0.12em] text-ink-muted">{label}</p>
      <p className="mt-2 text-3xl font-black text-ink">{value}</p>
      <p className="mt-1 text-xs text-ink-muted">{detail}</p>
    </Card>
  );
}

function formatScore(score: number | null | undefined): string {
  return score == null ? "-" : score.toFixed(1);
}

function nextAction({
  resumeReady,
  answersReady,
  currentJob,
}: {
  resumeReady: boolean;
  answersReady: boolean;
  currentJob?: JobPosting;
}) {
  if (!resumeReady) return { href: "/profile/resume", label: "Upload LaTeX resume" };
  if (!answersReady) return { href: "/profile#questions", label: "Complete profile answers" };
  if (currentJob) return { href: `/tailor/${encodeURIComponent(currentJob.job_id)}`, label: "Tailor latest job" };
  return { href: "/jobs", label: "Open captured jobs" };
}

function ReadinessRow({ ready, title, detail }: { ready: boolean; title: string; detail: string }) {
  return (
    <div className="grid grid-cols-[24px_minmax(0,1fr)] gap-3">
      <span
        className={`mt-0.5 flex h-5 w-5 items-center justify-center rounded-full ${
          ready ? "bg-primary text-primary-foreground" : "bg-budget-soft text-budget"
        }`}
      >
        {ready ? <CheckCircle2 className="h-4 w-4" /> : <ShieldCheck className="h-4 w-4" />}
      </span>
      <div className="min-w-0">
        <p className="font-bold text-ink">{title}</p>
        <p className="mt-0.5 break-words text-sm text-ink-muted">{detail}</p>
      </div>
    </div>
  );
}

function JobRow({ job }: { job: JobPosting }) {
  return (
    <Card className="relative overflow-hidden p-4 pl-5">
      <div className="absolute inset-y-0 left-0 w-1 bg-primary" aria-hidden="true" />
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.12em] text-ink-muted">
            <BriefcaseBusiness className="h-3.5 w-3.5" />
            {job.company}
          </div>
          <h3 className="mt-1 break-words text-lg font-extrabold text-ink">{job.title}</h3>
          <p className="mt-1 text-sm text-ink-muted">{job.location || "Location not listed"}</p>
        </div>
        <Link href={`/tailor/${encodeURIComponent(job.job_id)}`}>
          <Button size="sm">Tailor</Button>
        </Link>
      </div>
    </Card>
  );
}

function ApplicationRow({ app, job }: { app: ApplicationRecord; job?: JobPosting }) {
  return (
    <Card className="relative overflow-hidden p-4 pl-5">
      <div className="absolute inset-y-0 left-0 w-1 bg-accent" aria-hidden="true" />
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.12em] text-ink-muted">
            <ClipboardCheck className="h-3.5 w-3.5" />
            Application
          </div>
          <h3 className="mt-1 break-words font-extrabold text-ink">{job?.title ?? "Saved application"}</h3>
          <p className="mt-1 text-sm text-ink-muted">{job?.company ?? "Job details unavailable"}</p>
        </div>
        <ApplicationStatusBadge status={app.status} />
      </div>
    </Card>
  );
}
