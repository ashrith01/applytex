"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, ClipboardCheck } from "lucide-react";
import { api } from "@/lib/api";
import type { ApplicationStatus } from "@/lib/api/types";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ApplicationStatusBadge } from "@/components/shared/application-status-badge";

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

export default function ApplicationsPage() {
  const qc = useQueryClient();
  const apps = useQuery({ queryKey: ["applications"], queryFn: () => api.listApplications(100) });
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: () => api.listJobs(200) });

  const transition = useMutation({
    mutationFn: ({ id, status }: { id: string; status: ApplicationStatus }) =>
      api.transitionApplication(id, status),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["applications"] }),
  });

  const jobMap = new Map((jobs.data ?? []).map((j) => [j.job_id, j]));

  return (
    <div className="space-y-8">
      <div>
        <p className="font-mono text-xs uppercase tracking-[0.14em] text-ink-muted">manual submit required</p>
        <h1 className="mt-2 font-serif text-4xl font-black">Application review</h1>
        <p className="mt-3 max-w-2xl text-ink-muted">
          Track what still needs a resume, form scan, or final review. ApplyTeX ATS never submits the employer form for you.
        </p>
      </div>

      <div className="space-y-4">
        {(apps.data ?? []).map((app) => {
          const job = jobMap.get(app.job_id);
          const next = NEXT_STATUSES[app.status] ?? [];
          return (
            <Card key={app.application_id} className="p-5">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <p className="flex items-center gap-2 font-mono text-xs uppercase tracking-[0.12em] text-ink-muted">
                    <ClipboardCheck className="h-3.5 w-3.5" />
                    Review queue
                  </p>
                  <CardTitle className="mt-1">{job?.title ?? "Saved application"}</CardTitle>
                  <CardDescription>{job?.company ?? "Unknown company"}</CardDescription>
                  <p className="mt-2 text-sm text-ink-muted">{statusHint(app.status)}</p>
                </div>
                <ApplicationStatusBadge status={app.status} />
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                {job && (
                  <Link href={`/tailor/${encodeURIComponent(job.job_id)}?application_id=${app.application_id}`}>
                    <Button size="sm">
                      Tailor resume
                      <ArrowRight className="h-4 w-4" />
                    </Button>
                  </Link>
                )}
                {job && (
                  <Link href={`/jobs/${encodeURIComponent(job.job_id)}`}>
                    <Button size="sm" variant="secondary">Job details</Button>
                  </Link>
                )}
              </div>
              {next.length > 0 && (
                <details className="mt-4 rounded-md border border-border bg-surface-muted p-3">
                  <summary className="cursor-pointer text-xs font-bold uppercase tracking-[0.12em] text-ink-muted">
                    Manual status controls
                  </summary>
                  <div className="mt-3 flex flex-wrap gap-2">
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
                </details>
              )}
            </Card>
          );
        })}
        {!apps.data?.length && (
          <Card className="p-5 text-sm text-ink-muted">
            No applications yet. Capturing a job from the extension will create the first tracked review.
          </Card>
        )}
      </div>
    </div>
  );
}

function statusHint(status: ApplicationStatus): string {
  if (status === "needs_input") return "Some required answers still need review before autofill.";
  if (status === "ready_for_review") return "Review the employer page before submitting manually.";
  if (status === "resume_ready") return "Resume is ready; scan the application form from the extension.";
  if (status === "form_scanned") return "Application fields were scanned and can be reviewed.";
  if (status === "submitted") return "Marked submitted after manual review.";
  if (status === "skipped") return "Skipped for now.";
  if (status === "failed" || status === "blocked") return "Needs manual attention.";
  return "Continue the resume and form review workflow.";
}
