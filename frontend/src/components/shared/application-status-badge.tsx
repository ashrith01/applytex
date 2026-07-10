import type { ApplicationStatus } from "@/lib/api/types";
import { Badge } from "@/components/ui/badge";

const labels: Record<ApplicationStatus, string> = {
  discovered: "Discovered",
  scored: "Scored",
  selected: "Selected",
  resume_ready: "Resume ready",
  form_scanned: "Form scanned",
  needs_input: "Needs input",
  ready_for_review: "Ready for review",
  approved: "Approved",
  submitting: "Submitting",
  submitted: "Submitted",
  blocked: "Blocked",
  failed: "Failed",
  skipped: "Skipped",
};

export function ApplicationStatusBadge({ status }: { status: ApplicationStatus }) {
  const tone =
    status === "approved" || status === "submitted"
      ? "success"
      : status === "needs_input" || status === "blocked" || status === "failed"
        ? "warning"
        : "default";
  return <Badge tone={tone}>{labels[status]}</Badge>;
}
