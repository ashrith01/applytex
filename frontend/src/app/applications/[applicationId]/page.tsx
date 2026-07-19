"use client";

import Link from "next/link";
import { use, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  CalendarClock,
  CheckCircle2,
  ClipboardList,
  FileText,
  ListChecks,
  NotebookPen,
} from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth/profile-context";
import type { ApplicationArtifact, ApplicationDetail, ApplicationRecord } from "@/lib/api/types";
import { ApplicationStatusBadge } from "@/components/shared/application-status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";

export default function ApplicationDetailPage({
  params,
}: {
  params: Promise<{ applicationId: string }>;
}) {
  const { applicationId } = use(params);
  const { profileId } = useAuth();
  const qc = useQueryClient();
  const [notesDraft, setNotesDraft] = useState<string | null>(null);
  const [taskTitle, setTaskTitle] = useState("");

  const detail = useQuery({
    queryKey: ["application", applicationId, profileId],
    queryFn: () => api.getApplication(applicationId, profileId),
    enabled: !!profileId,
  });

  const saveNotes = useMutation({
    mutationFn: () =>
      api.patchApplication(
        applicationId,
        { notes: notesDraft ?? appNotes(detail.data) },
        profileId,
      ),
    onSuccess: () => {
      setNotesDraft(null);
      qc.invalidateQueries({ queryKey: ["application", applicationId, profileId] });
    },
  });

  const addTask = useMutation({
    mutationFn: () => api.createApplicationTask(applicationId, { title: taskTitle }, profileId),
    onSuccess: () => {
      setTaskTitle("");
      qc.invalidateQueries({ queryKey: ["application", applicationId, profileId] });
    },
  });

  const scoreApp = useMutation({
    mutationFn: () => api.scoreApplication(applicationId, profileId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["application", applicationId, profileId] });
      qc.invalidateQueries({ queryKey: ["applications"] });
      qc.invalidateQueries({ queryKey: ["applications-health"] });
    },
  });

  if (detail.isLoading) return <p className="text-ink-muted">Loading application…</p>;
  if (detail.isError || !detail.data) return <p className="text-red-600">Application not found.</p>;

  const data = detail.data;
  const app = data.application;
  const job = data.job;
  const latestResume = data.artifacts.find((artifact) => artifact.type === "tailored_resume");

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.14em] text-ink-muted">
            application workspace
          </p>
          <h1 className="mt-2 max-w-4xl text-3xl font-extrabold leading-tight">
            {job?.title || app.job_title || "Tracked application"}
          </h1>
          <p className="mt-2 text-ink-muted">
            {job?.company || app.company || "Unknown company"} / {app.stage.replace(/_/g, " ")}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <ApplicationStatusBadge status={app.status} />
          <Badge tone={latestResume?.status === "approved" ? "success" : "muted"}>
            {latestResume ? latestResume.status.replace(/_/g, " ") : "no artifact"}
          </Badge>
        </div>
      </div>

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <Card>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <CardTitle>Tracker fields</CardTitle>
              <CardDescription className="mt-2">
                Keep the lightweight CRM facts close to the resume and form review.
              </CardDescription>
            </div>
            {job && (
              <Link href={`/tailor/${encodeURIComponent(job.job_id)}?application_id=${app.application_id}`}>
                <Button size="sm">
                  Tailor resume
                  <ArrowRight className="h-4 w-4" />
                </Button>
              </Link>
            )}
            <Button
              size="sm"
              variant="secondary"
              onClick={() => scoreApp.mutate()}
              disabled={scoreApp.isPending}
            >
              {scoreApp.isPending ? "Scoring..." : "Refresh score"}
            </Button>
          </div>
          <InfoGrid app={app} data={data} />
          {scoreApp.isError && (
            <p className="mt-3 text-sm text-red-600">
              Score refresh needs a saved LaTeX profile resume.
            </p>
          )}
        </Card>

        <Card className="bg-surface-source">
          <CardTitle className="flex items-center gap-2">
            <ListChecks className="h-5 w-5 text-primary" />
            Next actions
          </CardTitle>
          <div className="mt-4 space-y-3">
            {data.tasks.filter((task) => task.status === "open").slice(0, 4).map((task) => (
              <div key={task.task_id} className="rounded-md border border-primary/15 bg-white px-3 py-2">
                <p className="text-sm font-bold">{task.title}</p>
                <p className="mt-1 text-xs text-ink-muted">{task.category.replace(/_/g, " ")}</p>
              </div>
            ))}
            {!data.tasks.some((task) => task.status === "open") && (
              <p className="text-sm text-ink-muted">No open tasks.</p>
            )}
            <div className="flex gap-2">
              <input
                className="min-w-0 flex-1 rounded-md border border-border bg-white px-3 py-2 text-sm"
                value={taskTitle}
                onChange={(event) => setTaskTitle(event.target.value)}
                placeholder="Add follow-up task"
              />
              <Button
                size="sm"
                onClick={() => addTask.mutate()}
                disabled={!taskTitle.trim() || addTask.isPending}
              >
                Add
              </Button>
            </div>
          </div>
        </Card>
      </section>

      <section className="grid gap-4 xl:grid-cols-3">
        <Card>
          <CardTitle className="flex items-center gap-2">
            <FileText className="h-5 w-5 text-accent" />
            Artifacts
          </CardTitle>
          <div className="mt-4 space-y-3">
            {data.artifacts.map((artifact) => (
              <ArtifactRow key={artifact.artifact_id} artifact={artifact} />
            ))}
            {!data.artifacts.length && (
              <p className="text-sm text-ink-muted">No resume or letter artifacts yet.</p>
            )}
          </div>
        </Card>

        <Card>
          <CardTitle className="flex items-center gap-2">
            <ClipboardList className="h-5 w-5 text-budget" />
            Latest form scan
          </CardTitle>
          {data.latest_form_scan ? (
            <div className="mt-4 space-y-3">
              <p className="text-sm text-ink-muted">
                {data.latest_form_scan.questions.length} fields from {data.latest_form_scan.provider}.
              </p>
              <div className="grid grid-cols-2 gap-2 text-sm">
                <Metric label="Required" value={String(data.latest_form_scan.questions.filter((q) => q.required).length)} />
                <Metric label="Sensitive" value={String(data.latest_form_scan.questions.filter((q) => q.sensitive).length)} />
              </div>
            </div>
          ) : (
            <p className="mt-4 text-sm text-ink-muted">
              Scan the employer form from the extension to populate field review history.
            </p>
          )}
        </Card>

        <Card>
          <CardTitle className="flex items-center gap-2">
            <NotebookPen className="h-5 w-5 text-primary" />
            Notes
          </CardTitle>
          <textarea
            className="mt-4 min-h-[160px] w-full rounded-md border border-border bg-white px-3 py-2 text-sm"
            value={notesDraft ?? app.notes}
            onChange={(event) => setNotesDraft(event.target.value)}
            placeholder="Add context, recruiter names, concerns, or follow-up notes."
          />
          <Button
            className="mt-3 w-full"
            variant="secondary"
            onClick={() => saveNotes.mutate()}
            disabled={saveNotes.isPending}
          >
            Save notes
          </Button>
        </Card>
      </section>

      <Card>
        <CardTitle className="flex items-center gap-2">
          <CalendarClock className="h-5 w-5 text-accent" />
          Timeline
        </CardTitle>
        <div className="mt-5 space-y-3">
          {data.events.map((event) => (
            <div key={event.event_id} className="grid gap-3 border-l-2 border-border pl-4 md:grid-cols-[180px_minmax(0,1fr)]">
              <p className="font-mono text-xs text-ink-muted">{formatDate(event.created_at)}</p>
              <div>
                <p className="font-bold">{event.label}</p>
                {event.detail && <p className="mt-1 text-sm text-ink-muted">{event.detail}</p>}
              </div>
            </div>
          ))}
          {!data.events.length && <p className="text-sm text-ink-muted">No activity recorded yet.</p>}
        </div>
      </Card>
    </div>
  );
}

function InfoGrid({ app, data }: { app: ApplicationRecord; data: ApplicationDetail }) {
  const job = data.job;
  const items = [
    ["Company", job?.company || app.company],
    ["Provider", job?.provider || app.provider],
    ["Location", job?.location || app.location],
    ["Workplace", job?.workplace_type || app.workplace_type],
    ["Salary", app.salary_range],
    ["Priority", app.priority],
    ["Excitement", `${app.excitement}/5`],
    ["Current resume score", formatScore(app.current_resume_score)],
    ["Tailored score", formatScore(app.tailored_resume_score)],
    ["Score updated", formatDate(app.score_updated_at)],
    ["Required gaps", app.required_missing.length],
    ["Preferred gaps", app.preferred_missing.length],
    ["Keyword gaps", app.keyword_misses.length],
    ["Missing answers", app.missing_answers_count],
    ["Deadline", app.deadline],
    ["Next follow-up", app.next_action_at],
    ["Last activity", formatDate(app.last_activity_at)],
  ];

  return (
    <dl className="mt-5 grid gap-x-8 gap-y-4 md:grid-cols-3">
      {items.map(([label, value]) => (
        <div key={label} className="min-w-0">
          <dt className="text-xs font-bold uppercase tracking-wide text-ink-muted">{label}</dt>
          <dd className="mt-1 break-words text-sm font-semibold text-ink">
            {value || "Not set"}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function appNotes(data?: ApplicationDetail) {
  return data?.application.notes ?? "";
}

function ArtifactRow({ artifact }: { artifact: ApplicationArtifact }) {
  return (
    <div className="rounded-md border border-border bg-white px-3 py-2">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="break-words text-sm font-bold">{artifact.filename || artifact.type}</p>
          <p className="mt-1 text-xs text-ink-muted">
            {artifact.type.replace(/_/g, " ")} / {artifact.status.replace(/_/g, " ")}
          </p>
        </div>
        {artifact.status === "approved" ? <CheckCircle2 className="h-4 w-4 shrink-0 text-primary" /> : null}
      </div>
      {artifact.ats_after?.score != null && (
        <p className="mt-2 text-xs font-bold text-ink-muted">Fit after {artifact.ats_after.score.toFixed(1)}</p>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-white px-3 py-2">
      <p className="font-mono text-xs uppercase tracking-wide text-ink-muted">{label}</p>
      <p className="mt-1 text-xl font-black">{value}</p>
    </div>
  );
}

function formatDate(value?: string | null) {
  if (!value) return "Not set";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function formatScore(score?: number | null) {
  return score == null ? "Not scored" : score.toFixed(1);
}
