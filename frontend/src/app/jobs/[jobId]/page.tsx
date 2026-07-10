"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { use } from "react";
import { api } from "@/lib/api";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

export default function JobDetailPage({ params }: { params: Promise<{ jobId: string }> }) {
  const { jobId } = use(params);
  const qc = useQueryClient();
  const job = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.getJob(jobId),
  });

  const createApp = useMutation({
    mutationFn: () => api.createApplication({ job_id: jobId }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["applications"] }),
  });

  if (job.isLoading) return <p className="text-ink-muted">Loading job…</p>;
  if (job.isError || !job.data) return <p className="text-red-600">Job not found.</p>;

  const j = job.data;

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.14em] text-ink-muted">captured posting</p>
          <h1 className="mt-2 font-serif text-4xl font-black leading-tight">{j.title}</h1>
          <p className="mt-2 text-ink-muted">{j.company} / {j.provider}</p>
        </div>
        <div className="flex gap-2">
          <Link href={`/tailor/${encodeURIComponent(jobId)}`}>
            <Button>Tailor resume</Button>
          </Link>
          <Button variant="secondary" onClick={() => createApp.mutate()} disabled={createApp.isPending}>
            Track application
          </Button>
        </div>
      </div>

      <Card>
        <CardTitle>Details</CardTitle>
        <CardDescription className="mt-2">
          {j.location || "No location"} / {j.workplace_type}
        </CardDescription>
        <div className="mt-4 flex flex-wrap gap-3 text-sm">
          <a href={j.source_url} target="_blank" rel="noreferrer" className="font-semibold text-ink underline">
            View posting
          </a>
          <a href={j.apply_url} target="_blank" rel="noreferrer" className="font-semibold text-ink underline">
            Apply page
          </a>
        </div>
      </Card>

      <Card>
        <CardTitle>Description</CardTitle>
        <pre className="mt-4 max-h-[520px] overflow-auto whitespace-pre-wrap text-sm text-ink-muted">
          {j.description}
        </pre>
      </Card>
    </div>
  );
}
