import { Suspense } from "react";
import TailorPageClient from "./tailor-client";

export default function TailorPage({ params }: { params: Promise<{ jobId: string }> }) {
  return (
    <Suspense fallback={<p className="text-ink-muted">Loading tailor flow...</p>}>
      <TailorPageClient params={params} />
    </Suspense>
  );
}
