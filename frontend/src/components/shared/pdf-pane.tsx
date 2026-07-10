"use client";

import { useEffect, useRef, useState } from "react";

interface PdfPaneProps {
  base64?: string | null;
  className?: string;
}

export function PdfPane({ base64, className }: PdfPaneProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!base64) return;
    let cancelled = false;

    async function renderPdf() {
      setLoading(true);
      setError(null);
      try {
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = `//cdnjs.cloudflare.com/ajax/libs/pdf.js/${pdfjs.version}/pdf.worker.min.mjs`;
        const binary = atob(base64!);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
        const doc = await pdfjs.getDocument({ data: bytes }).promise;
        const page = await doc.getPage(1);
        const viewport = page.getViewport({ scale: 1.25 });
        const canvas = canvasRef.current;
        if (!canvas || cancelled) return;
        const ctx = canvas.getContext("2d");
        if (!ctx) return;
        canvas.height = viewport.height;
        canvas.width = viewport.width;
        await page.render({ canvasContext: ctx, viewport }).promise;
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to render PDF");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    renderPdf();
    return () => {
      cancelled = true;
    };
  }, [base64]);

  if (!base64) {
    return (
      <div className={`flex min-h-[480px] items-center justify-center rounded-card border border-dashed border-border bg-surface-muted text-sm text-ink-muted ${className ?? ""}`}>
        PDF preview will appear after a successful one-page render.
      </div>
    );
  }

  return (
    <div className={`overflow-auto rounded-card border border-border bg-white p-4 ${className ?? ""}`}>
      {loading && <p className="mb-2 text-sm text-ink-muted">Rendering PDF…</p>}
      {error && <p className="mb-2 text-sm text-red-600">{error}</p>}
      <canvas ref={canvasRef} className="mx-auto max-w-full" />
    </div>
  );
}
