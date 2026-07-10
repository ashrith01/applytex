"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { FileCode2, FileText, ShieldCheck } from "lucide-react";
import { useAuth } from "@/lib/auth/profile-context";
import { api } from "@/lib/api";
import type { ProfileResumeUploadResponse } from "@/lib/api/types";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

export default function ProfileResumePage() {
  const { profileId, refresh } = useAuth();
  const qc = useQueryClient();
  const texInputRef = useRef<HTMLInputElement>(null);
  const pdfInputRef = useRef<HTMLInputElement>(null);
  const [overwrite, setOverwrite] = useState(false);
  const [lastUpload, setLastUpload] = useState<ProfileResumeUploadResponse | null>(null);

  const resume = useQuery({
    queryKey: ["profile-resume", profileId],
    queryFn: () => api.getProfileResume(profileId!),
    enabled: !!profileId,
  });

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadProfileResume(file, profileId!, overwrite),
    onSuccess: async (data) => {
      setLastUpload(data);
      await qc.invalidateQueries({ queryKey: ["profile-resume", profileId] });
      await qc.invalidateQueries({ queryKey: ["profile", profileId] });
      await qc.invalidateQueries({ queryKey: ["profile-setup", profileId] });
      await refresh();
    },
  });

  function handleFile(file: File | undefined) {
    if (file) upload.mutate(file);
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <p className="font-mono text-xs uppercase tracking-[0.14em] text-ink-muted">source setup</p>
        <h1 className="mt-2 font-serif text-4xl font-black">Upload the resume source</h1>
        <p className="mt-3 text-ink-muted">
          Use a .tex file for job-specific tailoring. ApplyTeX ATS maps editable statements, preserves the LaTeX structure, and blocks results that exceed one page.
        </p>
      </div>

      <Card className="bg-surface-source">
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="h-5 w-5 text-primary" />
          Current resume
        </CardTitle>
        <CardDescription className="mt-2">
          {resume.data?.resume_filename || "No file uploaded"}
          {resume.data?.has_latex_source && " / LaTeX source available"}
          {resume.data?.has_pdf && " / PDF available"}
        </CardDescription>
        {resume.data?.resume_updated_at && (
          <p className="mt-2 text-xs text-ink-muted">Updated {resume.data.resume_updated_at}</p>
        )}
      </Card>

      <Card>
        <CardTitle>Extraction options</CardTitle>
        <label className="mt-3 flex items-start gap-3 text-sm">
          <input
            type="checkbox"
            className="mt-1"
            checked={overwrite}
            onChange={(e) => setOverwrite(e.target.checked)}
          />
          <span>
            Overwrite existing profile fields with extracted resume data. When unchecked, only empty
            profile fields are filled.
          </span>
        </label>
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardTitle className="flex items-center gap-2">
            <FileCode2 className="h-5 w-5 text-primary" />
            Upload LaTeX (.tex)
          </CardTitle>
          <CardDescription className="mt-2">
            Best for tailoring. Parses editable statements and extracts profile details.
          </CardDescription>
          <input
            ref={texInputRef}
            type="file"
            accept=".tex"
            className="hidden"
            onChange={(e) => {
              handleFile(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
          <Button
            className="mt-4 w-full"
            disabled={upload.isPending}
            onClick={() => texInputRef.current?.click()}
          >
            Choose .tex file
          </Button>
        </Card>

        <Card>
          <CardTitle className="flex items-center gap-2">
            <FileText className="h-5 w-5 text-ink-muted" />
            Upload PDF
          </CardTitle>
          <CardDescription className="mt-2">
            For direct application upload. Text is extracted heuristically to prefill your profile.
          </CardDescription>
          <input
            ref={pdfInputRef}
            type="file"
            accept=".pdf,application/pdf"
            className="hidden"
            onChange={(e) => {
              handleFile(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
          <Button
            className="mt-4 w-full"
            variant="secondary"
            disabled={upload.isPending}
            onClick={() => pdfInputRef.current?.click()}
          >
            Choose PDF file
          </Button>
        </Card>
      </div>

      {upload.isPending && (
        <Card className="border-primary/30 bg-primary/5">
          <p className="text-sm font-semibold text-ink">Uploading and extracting profile data...</p>
        </Card>
      )}
      {upload.isError && (
        <Card className="border-red-200 bg-red-50">
          <p className="text-sm text-red-700">{(upload.error as Error).message}</p>
        </Card>
      )}
      {lastUpload && upload.isSuccess && (
        <Card className="border-emerald-200 bg-emerald-50/50">
          <CardTitle className="text-emerald-900">Resume saved</CardTitle>
          <CardDescription className="mt-2 text-emerald-900/80">
            {lastUpload.prefill_labels.length
              ? "Profile updated from extracted resume content:"
              : "Resume stored. No new profile fields were filled (existing values were kept or nothing could be extracted)."}
          </CardDescription>
          {lastUpload.prefill_labels.length > 0 && (
            <div className="mt-4 flex flex-wrap gap-2">
              {lastUpload.prefill_labels.map((label) => (
                <Badge key={label} tone="success">
                  {label}
                </Badge>
              ))}
            </div>
          )}
          <Link href="/profile" className="mt-4 inline-block">
            <Button size="sm">Review profile</Button>
          </Link>
        </Card>
      )}

      <div className="flex gap-3">
        <Button variant="secondary" onClick={() => window.history.back()}>
          Back
        </Button>
        <Link href="/profile">
          <Button variant="ghost">Open profile</Button>
        </Link>
      </div>
    </div>
  );
}
