"use client";

import { useEffect, useRef, useState } from "react";
import type { PDFDocumentProxy } from "pdfjs-dist";

const RENDER_SCALE = 1.25;

export interface HighlightRect {
  page: number;
  x: number;
  y: number;
  width: number;
  height: number;
}

interface PdfPaneProps {
  base64?: string | null;
  className?: string;
  highlightRects?: HighlightRect[];
  onDocumentLoaded?: (doc: PDFDocumentProxy) => void;
  onPageRendered?: (pageIndex: number, canvas: HTMLCanvasElement) => void;
}

export function PdfPane({
  base64,
  className,
  highlightRects,
  onDocumentLoaded,
  onPageRendered,
}: PdfPaneProps) {
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [pageCount, setPageCount] = useState(0);
  const docRef = useRef<PDFDocumentProxy | null>(null);
  const canvasRefs = useRef<Map<number, HTMLCanvasElement>>(new Map());
  const renderGenRef = useRef(0);

  useEffect(() => {
    if (!base64) {
      setPageCount(0);
      docRef.current = null;
      return;
    }

    const gen = ++renderGenRef.current;
    setLoading(true);
    setError(null);
    setPageCount(0);
    canvasRefs.current.clear();

    (async () => {
      try {
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = `//cdnjs.cloudflare.com/ajax/libs/pdf.js/${pdfjs.version}/pdf.worker.min.mjs`;
        const binary = atob(base64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
        const doc = await pdfjs.getDocument({ data: bytes }).promise;
        if (gen !== renderGenRef.current) return;
        docRef.current = doc;
        onDocumentLoaded?.(doc);
        setPageCount(doc.numPages);
      } catch (err) {
        if (gen === renderGenRef.current) {
          setError(err instanceof Error ? err.message : "Failed to load PDF");
          setLoading(false);
        }
      }
    })();
  }, [base64, onDocumentLoaded]);

  useEffect(() => {
    if (pageCount === 0 || !docRef.current) return;

    const doc = docRef.current;
    const gen = renderGenRef.current;

    (async () => {
      try {
        for (let pageNum = 1; pageNum <= doc.numPages; pageNum += 1) {
          if (gen !== renderGenRef.current) return;
          const page = await doc.getPage(pageNum);
          const viewport = page.getViewport({ scale: RENDER_SCALE });
          const canvas = canvasRefs.current.get(pageNum);
          if (!canvas || gen !== renderGenRef.current) continue;
          const ctx = canvas.getContext("2d");
          if (!ctx) continue;
          canvas.height = viewport.height;
          canvas.width = viewport.width;
          await page.render({ canvasContext: ctx, viewport }).promise;
          if (gen !== renderGenRef.current) return;
          onPageRendered?.(pageNum - 1, canvas);
        }
      } finally {
        if (gen === renderGenRef.current) setLoading(false);
      }
    })();
  }, [pageCount, onPageRendered]);

  useEffect(() => {
    if (!highlightRects || highlightRects.length === 0 || loading || pageCount === 0) return;

    for (const [pageNum, canvas] of canvasRefs.current.entries()) {
      const ctx = canvas.getContext("2d");
      if (!ctx) continue;
      const pageRects = highlightRects.filter((r) => r.page === pageNum);
      if (pageRects.length === 0) continue;
      for (const rect of pageRects) {
        const x = rect.x * RENDER_SCALE;
        const y = rect.y * RENDER_SCALE;
        const w = rect.width * RENDER_SCALE;
        const h = rect.height * RENDER_SCALE;
        ctx.fillStyle = "rgba(34,197,94,0.35)";
        ctx.fillRect(x, y, w, h);
        ctx.strokeStyle = "rgba(34,197,94,0.8)";
        ctx.lineWidth = 1.5;
        ctx.strokeRect(x, y, w, h);
      }
    }
  }, [highlightRects, loading, pageCount]);

  if (!base64) {
    return (
      <div
        className={`flex min-h-[480px] items-center justify-center rounded-card border border-dashed border-border bg-surface-muted text-sm text-ink-muted ${className ?? ""}`}
      >
        PDF preview will appear after a successful one-page render.
      </div>
    );
  }

  return (
    <div className={`overflow-auto rounded-card border border-border bg-white p-4 ${className ?? ""}`}>
      {loading && <p className="mb-2 text-sm text-ink-muted">Rendering PDF…</p>}
      {error && <p className="mb-2 text-sm text-red-600">{error}</p>}
      <div className="flex flex-col items-center gap-4">
        {Array.from({ length: pageCount }, (_, i) => i + 1).map((pageNum) => (
          <canvas
            key={pageNum}
            ref={(el) => {
              if (el) canvasRefs.current.set(pageNum, el);
              else canvasRefs.current.delete(pageNum);
            }}
            className="mx-auto max-w-full shadow-sm"
          />
        ))}
      </div>
    </div>
  );
}
