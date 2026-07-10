"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ArrowRight, BriefcaseBusiness, Search } from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

export default function JobsPage() {
  const qc = useQueryClient();
  const [text, setText] = useState("machine learning engineer");
  const [provider, setProvider] = useState<"greenhouse" | "lever" | "ashby">("greenhouse");
  const [boardToken, setBoardToken] = useState("");
  const [company, setCompany] = useState("");

  const jobs = useQuery({ queryKey: ["jobs"], queryFn: () => api.listJobs(100) });

  const search = useMutation({
    mutationFn: () =>
      api.searchJobs({
        query: { text, remote_only: false, limit: 50, role_keywords: [], locations: [], target_roles: [] },
        sources: [{ provider, board_token: boardToken, company }],
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
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
            Open a supported LinkedIn, Greenhouse, Lever, or Ashby job and use the page panel to capture the posting and scan the form.
          </CardDescription>
        </Card>
      </div>

      <div className="space-y-3">
        {(jobs.data ?? []).map((job) => (
          <Card key={job.job_id} className="p-4">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <p className="font-mono text-xs uppercase tracking-[0.12em] text-ink-muted">{job.company}</p>
                <CardTitle className="mt-1 text-lg">{job.title}</CardTitle>
                <CardDescription>
                  {job.location || "Location not listed"} / {job.provider}
                </CardDescription>
              </div>
              <div className="flex gap-2">
                <Link href={`/jobs/${encodeURIComponent(job.job_id)}`}>
                  <Button variant="secondary" size="sm">Details</Button>
                </Link>
                <Link href={`/tailor/${encodeURIComponent(job.job_id)}`}>
                  <Button size="sm">
                    Tailor
                    <ArrowRight className="h-4 w-4" />
                  </Button>
                </Link>
              </div>
            </div>
          </Card>
        ))}
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
