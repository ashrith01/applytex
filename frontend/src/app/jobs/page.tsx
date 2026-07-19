"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ArrowRight, BriefcaseBusiness, Search } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth/profile-context";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ApplicationStatusBadge } from "@/components/shared/application-status-badge";
import type { ApplicationRecord } from "@/lib/api/types";

export default function JobsPage() {
  const qc = useQueryClient();
  const { profileId } = useAuth();
  const [text, setText] = useState("machine learning engineer");
  const [provider, setProvider] = useState<"greenhouse" | "lever" | "ashby">("greenhouse");
  const [boardToken, setBoardToken] = useState("");
  const [company, setCompany] = useState("");

  const jobs = useQuery({
    queryKey: ["jobs", profileId],
    queryFn: () => api.listJobs(100, profileId),
    enabled: !!profileId,
  });
  const applications = useQuery({
    queryKey: ["applications", profileId],
    queryFn: () => api.listApplications(200, profileId),
    enabled: !!profileId,
  });

  const search = useMutation({
    mutationFn: () =>
      api.searchJobs({
        query: { text, remote_only: false, limit: 50, role_keywords: [], locations: [], target_roles: [] },
        sources: [{ provider, board_token: boardToken, company }],
      }, profileId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs", profileId] }),
  });

  return (
    <div className="space-y-8">
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.14em] text-ink-muted">job evidence</p>
          <h1 className="mt-2 font-serif text-4xl font-black leading-tight">Captured jobs</h1>
          <p className="mt-3 max-w-2xl text-ink-muted">
            Pick the posting that should drive the next resume rewrite. The browser extension is the fastest way to capture a live application page.
          </p>
        </div>
        <Card className="bg-surface-source">
          <CardTitle className="flex items-center gap-2">
            <BriefcaseBusiness className="h-5 w-5" />
            Extension-first MVP
          </CardTitle>
          <CardDescription className="mt-2">
            Capture from LinkedIn or an ATS page, then review autofill in the panel. Provider depth
            varies: Workday/Ashby/Greenhouse/Lever are deepest; aggregators are experimental.
          </CardDescription>
        </Card>
      </div>

      <Card className="p-5">
        <CardTitle className="text-base">Search preferences</CardTitle>
        <CardDescription className="mt-2">
          Defaults are broad (Remote - US, no automatic senior-title exclusion). Edit role, location,
          and exclusion terms on the Profile page so board search and scoring match your goals.
        </CardDescription>
        <div className="mt-4">
          <Link href="/profile">
            <Button variant="secondary">Edit preferences on Profile</Button>
          </Link>
        </div>
      </Card>

      <div className="space-y-3">
        {(jobs.data ?? []).map((job) => {
          const application = (applications.data ?? []).find((item) => item.job_id === job.job_id);
          return (
          <Card key={job.job_id} className="p-4">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <p className="font-mono text-xs uppercase tracking-[0.12em] text-ink-muted">{job.company}</p>
                <CardTitle className="mt-1 text-lg">{job.title}</CardTitle>
                <CardDescription>
                  {job.location || "Location not listed"} / {job.provider}
                </CardDescription>
                {application && (
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <ApplicationStatusBadge status={application.status} />
                    <span className="rounded-md border border-border bg-surface-muted px-2 py-1 text-xs font-bold text-ink-muted">
                      Current score {formatScore(application.current_resume_score)}
                    </span>
                  </div>
                )}
              </div>
              <div className="flex gap-2">
                <Link href={`/jobs/${encodeURIComponent(job.job_id)}`}>
                  <Button variant="secondary" size="sm">Details</Button>
                </Link>
                <Link href={tailorHref(job.job_id, application)}>
                  <Button size="sm">
                    Tailor
                    <ArrowRight className="h-4 w-4" />
                  </Button>
                </Link>
              </div>
            </div>
          </Card>
          );
        })}
        {!jobs.data?.length && (
          <Card className="p-5 text-sm text-ink-muted">
            No saved jobs yet. Capture one from the extension, or use the local board search below.
          </Card>
        )}
      </div>

      <details className="rounded-card border border-border bg-surface-card p-5">
        <summary className="cursor-pointer text-sm font-extrabold text-ink">
          Local board search
        </summary>
        <p className="mt-2 text-sm text-ink-muted">
          Development helper for public Greenhouse, Lever, and Ashby boards. Most MVP users should capture jobs from the extension.
        </p>
        <div className="mt-5 grid gap-4 md:grid-cols-2">
          <label className="text-sm font-semibold">
            Query
            <input
              className="mt-1 w-full rounded-md border border-border bg-white px-3 py-2"
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </label>
          <label className="text-sm font-semibold">
            Provider
            <select
              className="mt-1 w-full rounded-md border border-border bg-white px-3 py-2"
              value={provider}
              onChange={(e) => setProvider(e.target.value as typeof provider)}
            >
              <option value="greenhouse">Greenhouse</option>
              <option value="lever">Lever</option>
              <option value="ashby">Ashby</option>
            </select>
          </label>
          <label className="text-sm font-semibold">
            Company board slug
            <input
              className="mt-1 w-full rounded-md border border-border bg-white px-3 py-2"
              value={boardToken}
              onChange={(e) => setBoardToken(e.target.value)}
              placeholder="company-board-token"
            />
          </label>
          <label className="text-sm font-semibold">
            Company name
            <input
              className="mt-1 w-full rounded-md border border-border bg-white px-3 py-2"
              value={company}
              onChange={(e) => setCompany(e.target.value)}
            />
          </label>
        </div>
        <Button
          className="mt-4"
          onClick={() => search.mutate()}
          disabled={search.isPending || !boardToken || !company}
        >
          <Search className="h-4 w-4" />
          {search.isPending ? "Searching..." : "Search board"}
        </Button>
        {search.data?.errors?.length ? (
          <p className="mt-2 text-sm text-budget">
            Partial errors: {search.data.errors.map((e) => e.message).join("; ")}
          </p>
        ) : null}
      </details>
    </div>
  );
}

function tailorHref(jobId: string, application?: ApplicationRecord) {
  const base = `/tailor/${encodeURIComponent(jobId)}`;
  return application ? `${base}?application_id=${encodeURIComponent(application.application_id)}` : base;
}

function formatScore(score: number | null | undefined): string {
  return score == null ? "-" : score.toFixed(1);
}
