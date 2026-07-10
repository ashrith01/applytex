"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import type { AnalyzeResponse, OptimizeResponse, UploadResponse } from "@/lib/api/types";
import { FitScoreHero } from "@/components/shared/fit-score-hero";
import { SkillConfirmGrid } from "@/components/shared/skill-confirm-grid";
import { StatementDiffList } from "@/components/shared/statement-diff-list";
import { PdfPane } from "@/components/shared/pdf-pane";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { formatScore } from "@/lib/utils";

type Tab = "parse" | "fit" | "optimize" | "analysis" | "pdf";

export default function LabPage() {
  const [tab, setTab] = useState<Tab>("parse");
  const [jd, setJd] = useState("");
  const [upload, setUpload] = useState<UploadResponse | null>(null);
  const [analysis, setAnalysis] = useState<AnalyzeResponse | null>(null);
  const [confirmedSkills, setConfirmedSkills] = useState<string[]>([]);
  const [result, setResult] = useState<OptimizeResponse | null>(null);
  const [analysisMode, setAnalysisMode] = useState<"fast" | "deep">("fast");

  const uploadMutation = useMutation({
    mutationFn: (file: File) => api.uploadLatex(file),
    onSuccess: setUpload,
  });

  const analyzeMutation = useMutation({
    mutationFn: () =>
      api.analyzeLatex({
        session_id: upload!.session_id,
        job_description: jd,
        confirmed_skills: confirmedSkills,
        analysis_mode: analysisMode,
      }),
    onSuccess: (data) => {
      setAnalysis(data);
      setTab("fit");
    },
  });

  const optimizeMutation = useMutation({
    mutationFn: () =>
      api.optimizeLatex({
        session_id: upload!.session_id,
        job_description: jd,
        confirmed_skills: confirmedSkills,
      }),
    onSuccess: (data) => {
      setResult(data);
      setTab("optimize");
    },
  });

  const report = useQuery({
    queryKey: ["report", upload?.session_id],
    queryFn: () => api.getSessionReport(upload!.session_id),
    enabled: !!upload?.session_id && !!result,
  });

  const tabs: { id: Tab; label: string }[] = [
    { id: "parse", label: "Parse" },
    { id: "fit", label: "Fit Score" },
    { id: "optimize", label: "Optimize" },
    { id: "analysis", label: "Analysis" },
    { id: "pdf", label: "Optimized PDF" },
  ];

  return (
    <div className="space-y-8">
      <div>
        <div className="inline-flex rounded-pill border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-bold text-amber-900">
          Advanced / diagnostic
        </div>
        <h1 className="mt-3 text-3xl font-extrabold">Resume Lab</h1>
        <p className="mt-2 text-ink-muted">
          Upload a `.tex` resume, paste a job description, and inspect parsing, fit scoring, and optimization output.
        </p>
      </div>

      <Card>
        <div className="grid gap-4 lg:grid-cols-2">
          <div>
            <CardTitle>Resume (.tex)</CardTitle>
            <input
              type="file"
              accept=".tex"
              className="mt-3 block w-full text-sm"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) uploadMutation.mutate(f);
              }}
            />
            {upload && (
              <p className="mt-2 text-sm text-ink-muted">
                Session {upload.session_id.slice(0, 8)}… · {Object.keys(upload.editable).length} editable sections
              </p>
            )}
          </div>
          <div>
            <CardTitle>Job description</CardTitle>
            <textarea
              className="mt-3 min-h-[180px] w-full rounded-xl border border-border p-3 text-sm"
              value={jd}
              onChange={(e) => setJd(e.target.value)}
              placeholder="Paste a job description…"
            />
          </div>
        </div>
      </Card>

      <div className="flex flex-wrap gap-2 border-b border-border pb-3">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={`rounded-pill px-4 py-2 text-sm font-semibold ${
              tab === t.id ? "bg-primary/10 text-ink" : "text-ink-muted hover:bg-surface-muted"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "parse" && upload && (
        <Card>
          <CardTitle>Parsed structure</CardTitle>
          <pre className="mt-4 max-h-[480px] overflow-auto rounded-xl bg-surface-muted p-4 text-xs">
            {JSON.stringify({ page_budget: upload.page_budget, editable: upload.editable }, null, 2)}
          </pre>
        </Card>
      )}

      {tab === "fit" && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-4">
            <label className="text-sm font-semibold">
              Analysis mode
              <select
                className="ml-2 rounded-lg border border-border px-3 py-2"
                value={analysisMode}
                onChange={(e) => setAnalysisMode(e.target.value as "fast" | "deep")}
              >
                <option value="fast">Fast local</option>
                <option value="deep">Deep LLM</option>
              </select>
            </label>
            <Button
              onClick={() => analyzeMutation.mutate()}
              disabled={!upload || !jd.trim() || analyzeMutation.isPending}
            >
              {analyzeMutation.isPending ? "Analyzing…" : "Analyze resume against JD"}
            </Button>
          </div>
          {analysis && (
            <>
              <FitScoreHero score={analysis.baseline_ats.score ?? 0} />
              <Card>
                <CardTitle>Confirm skills before optimize</CardTitle>
                <div className="mt-4">
                  <SkillConfirmGrid
                    groups={analysis.skill_groups}
                    selected={confirmedSkills}
                    onChange={setConfirmedSkills}
                  />
                </div>
              </Card>
            </>
          )}
        </div>
      )}

      {tab === "optimize" && (
        <div className="space-y-4">
          <Button
            onClick={() => optimizeMutation.mutate()}
            disabled={!upload || !jd.trim() || optimizeMutation.isPending}
          >
            {optimizeMutation.isPending ? "Optimizing…" : "Run optimization"}
          </Button>
          {result && (
            <>
              <div className="grid gap-4 md:grid-cols-4">
                <Metric label="Fit after" value={`${result.ats_after?.score?.toFixed(1) ?? "—"}/100`} />
                <Metric label="Target met" value={result.ats_target_met ? "Yes" : "No"} />
                <Metric label="Changes" value={String(result.diff.length)} />
                <Metric label="Pages" value={String(result.page_count)} />
              </div>
              <StatementDiffList diffs={result.diff} />
            </>
          )}
        </div>
      )}

      {tab === "analysis" && report.data?.run_record && (
        <Card>
          <CardTitle>Optimization report</CardTitle>
          <div className="mt-4 grid gap-4 md:grid-cols-4">
            <Metric label="Before" value={formatScore(report.data.run_record.score_before as number)} />
            <Metric label="After" value={formatScore(report.data.run_record.score_after as number)} />
            <Metric label="Delta" value={formatScore(report.data.run_record.score_delta as number, true)} />
            <Metric label="Target" value={report.data.run_record.ats_target_met ? "Met" : "Not met"} />
          </div>
          <p className="mt-4 text-sm text-ink-muted">{String(report.data.run_record.report_summary ?? "")}</p>
        </Card>
      )}

      {tab === "pdf" && result && (
        <div className="space-y-4">
          <PdfPane base64={result.modified_pdf_b64} />
          {result.modified_latex && (
            <a
              href={`data:text/plain;charset=utf-8,${encodeURIComponent(result.modified_latex)}`}
              download="optimized_resume.tex"
            >
              <Button variant="secondary">Download .tex</Button>
            </a>
          )}
        </div>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <Card className="p-4 text-center">
      <div className="text-xs font-bold uppercase text-ink-muted">{label}</div>
      <div className="mt-1 text-2xl font-black">{value}</div>
    </Card>
  );
}
