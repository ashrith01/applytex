import { apiFetch, apiUpload } from "./client";
import type {
  ActiveProfileResponse,
  AnalyzeResponse,
  ApplicationRecord,
  ApplicationStatus,
  CandidateProfile,
  HealthResponse,
  JobPosting,
  JobSearchResult,
  OptimizeResponse,
  ProfilePatch,
  ProfileResumeInfo,
  ProfileResumeUploadResponse,
  ProfileSetupResponse,
  ProfileView,
  ReportResponse,
  StatusResponse,
  TailorSessionResponse,
  UploadResponse,
} from "./types";

export const api = {
  health: () => apiFetch<HealthResponse>("/health"),

  setActiveProfile: (profileId: string) =>
    apiFetch<ActiveProfileResponse>("/profile/active", {
      method: "PUT",
      body: JSON.stringify({ profile_id: profileId }),
    }),

  getActiveProfile: () => apiFetch<ActiveProfileResponse>("/profile/active"),

  getProfile: (profileId: string) =>
    apiFetch<CandidateProfile>(`/profile?profile_id=${encodeURIComponent(profileId)}`),

  getProfileView: (profileId: string) =>
    apiFetch<ProfileView>(`/profile/view?profile_id=${encodeURIComponent(profileId)}`),

  updateProfile: (profile: CandidateProfile) =>
    apiFetch<CandidateProfile>("/profile", {
      method: "PUT",
      body: JSON.stringify(profile),
    }),

  patchProfile: (profileId: string, patch: ProfilePatch) =>
    apiFetch<ProfileView>(`/profile?profile_id=${encodeURIComponent(profileId)}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),

  getProfileSetup: (profileId: string) =>
    apiFetch<ProfileSetupResponse>(
      `/profile/setup-questions?profile_id=${encodeURIComponent(profileId)}`,
    ),

  getProfileResume: (profileId: string) =>
    apiFetch<ProfileResumeInfo>(
      `/profile/resume?profile_id=${encodeURIComponent(profileId)}`,
    ),

  uploadProfileResume: (file: File, profileId: string, overwrite = false) => {
    const form = new FormData();
    form.append("file", file);
    return apiUpload<ProfileResumeUploadResponse>(
      `/profile/resume?profile_id=${encodeURIComponent(profileId)}&overwrite=${overwrite ? "true" : "false"}`,
      form,
      profileId,
    );
  },

  searchJobs: (payload: {
    query: Record<string, unknown>;
    sources: Record<string, unknown>[];
    use_saved_preferences?: boolean;
  }) =>
    apiFetch<JobSearchResult>("/jobs/search", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  listJobs: (limit = 50) => apiFetch<JobPosting[]>(`/jobs?limit=${limit}`),

  getJob: (jobId: string) => apiFetch<JobPosting>(`/jobs/${encodeURIComponent(jobId)}`),

  listApplications: (limit = 50) =>
    apiFetch<ApplicationRecord[]>(`/applications?limit=${limit}`),

  createApplication: (payload: {
    job_id: string;
    resume_session_id?: string | null;
    notes?: string;
  }) =>
    apiFetch<ApplicationRecord>("/applications", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  transitionApplication: (
    applicationId: string,
    status: ApplicationStatus,
    notes?: string,
  ) =>
    apiFetch<ApplicationRecord>(
      `/applications/${encodeURIComponent(applicationId)}/transition`,
      {
        method: "POST",
        body: JSON.stringify({ status, notes }),
      },
    ),

  uploadLatex: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return apiUpload<UploadResponse>("/latex/upload", form);
  },

  analyzeLatex: (payload: {
    job_description: string;
    session_id?: string;
    latex_source?: string;
    confirmed_skills?: string[];
    analysis_mode?: "fast" | "deep";
  }) =>
    apiFetch<AnalyzeResponse>("/latex/analyze", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  optimizeLatex: (payload: {
    session_id: string;
    job_description: string;
    confirmed_skills?: string[];
    allowed_stmt_ids?: string[];
    optimization_strategy?: string;
    reviewer_backend?: string | null;
  }) =>
    apiFetch<OptimizeResponse>("/latex/optimize", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  refineLatex: (
    sessionId: string,
    payload: {
      job_description: string;
      instruction: string;
      confirmed_skills?: string[];
      allowed_stmt_ids?: string[];
      scope_label?: string;
      latex_source?: string;
      job_keywords?: Record<string, unknown>;
    },
  ) =>
    apiFetch<OptimizeResponse>(`/latex/${encodeURIComponent(sessionId)}/refine`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getSessionStatus: (sessionId: string) =>
    apiFetch<StatusResponse>(`/latex/${encodeURIComponent(sessionId)}/status`),

  getSessionReport: (sessionId: string) =>
    apiFetch<ReportResponse>(`/latex/${encodeURIComponent(sessionId)}/report`),

  deleteSession: (sessionId: string) =>
    apiFetch<{ deleted: string }>(`/latex/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    }),

  createTailorSession: (payload: {
    job_id: string;
    profile_id?: string;
    application_id?: string;
  }) =>
    apiFetch<TailorSessionResponse>("/tailor/sessions", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getTailorSession: (sessionId: string) =>
    apiFetch<TailorSessionResponse>(`/tailor/sessions/${encodeURIComponent(sessionId)}`),

  updateTailorSession: (
    sessionId: string,
    payload: { confirmed_skills?: string[]; current_latex?: string },
  ) =>
    apiFetch<TailorSessionResponse>(`/tailor/sessions/${encodeURIComponent(sessionId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  optimizeTailorSession: (
    sessionId: string,
    payload?: { allowed_stmt_ids?: string[]; optimization_strategy?: string },
  ) =>
    apiFetch<TailorSessionResponse>(
      `/tailor/sessions/${encodeURIComponent(sessionId)}/optimize`,
      {
        method: "POST",
        body: JSON.stringify(payload ?? {}),
      },
    ),

  refineTailorSession: (
    sessionId: string,
    payload: { instruction: string; allowed_stmt_ids?: string[]; scope_label?: string },
  ) =>
    apiFetch<TailorSessionResponse>(
      `/tailor/sessions/${encodeURIComponent(sessionId)}/refine`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    ),
};
