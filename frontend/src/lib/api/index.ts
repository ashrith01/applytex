import { apiFetch, apiUpload } from "./client";
import type {
  ActiveProfileResponse,
  AnalyzeResponse,
  ApplicationArtifact,
  ApplicationArtifactStatus,
  ApplicationDetail,
  ApplicationEvent,
  ApplicationRecord,
  ApplicationScoreResponse,
  ApplicationStatus,
  ApplicationTask,
  ApplicationsHealthResponse,
  AuthLoginResponse,
  AuthStatusResponse,
  CandidateProfile,
  HealthResponse,
  JobPosting,
  JobSearchResult,
  OptimizeResponse,
  ProfilePatch,
  ProjectRankResponse,
  ProjectRecord,
  ProjectSyncResponse,
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

  getAuthStatus: (profileId?: string | null) =>
    apiFetch<AuthStatusResponse>(
      profileId
        ? `/auth/status?profile_id=${encodeURIComponent(profileId)}`
        : "/auth/status",
    ),

  login: (profileId: string, password: string, setPassword = false) =>
    apiFetch<AuthLoginResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({
        profile_id: profileId,
        password,
        set_password: setPassword,
      }),
    }),

  setActiveProfile: (profileId: string) =>
    apiFetch<ActiveProfileResponse>("/profile/active", {
      method: "PUT",
      body: JSON.stringify({ profile_id: profileId }),
      profileId,
    }),

  getActiveProfile: (profileId?: string | null) =>
    apiFetch<ActiveProfileResponse>("/profile/active", {
      profileId: profileId ?? undefined,
    }),

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

  listProfileProjects: (profileId?: string | null) => {
    const query = profileId ? `?profile_id=${encodeURIComponent(profileId)}` : "";
    return apiFetch<ProjectRecord[]>(`/profile/projects${query}`);
  },

  syncGithubProjects: (profileId?: string | null) => {
    const query = profileId ? `?profile_id=${encodeURIComponent(profileId)}` : "";
    return apiFetch<ProjectSyncResponse>(`/profile/projects/sync/github${query}`, {
      method: "POST",
    });
  },

  searchJobs: (
    payload: {
      query: Record<string, unknown>;
      sources: Record<string, unknown>[];
      use_saved_preferences?: boolean;
    },
    profileId?: string | null,
  ) =>
    apiFetch<JobSearchResult>("/jobs/search", {
      method: "POST",
      body: JSON.stringify(payload),
      profileId: profileId ?? undefined,
    }),

  listJobs: (limit = 50, profileId?: string | null) =>
    apiFetch<JobPosting[]>(`/jobs?limit=${limit}`, {
      profileId: profileId ?? undefined,
    }),

  getJob: (jobId: string) => apiFetch<JobPosting>(`/jobs/${encodeURIComponent(jobId)}`),

  listApplications: (limit = 50, profileId?: string | null) =>
    apiFetch<ApplicationRecord[]>(`/applications?limit=${limit}`, {
      profileId: profileId ?? undefined,
    }),

  getApplicationsHealth: (profileId?: string | null) =>
    apiFetch<ApplicationsHealthResponse>("/applications/health", {
      profileId: profileId ?? undefined,
    }),

  getApplication: (applicationId: string, profileId?: string | null) =>
    apiFetch<ApplicationDetail>(`/applications/${encodeURIComponent(applicationId)}`, {
      profileId: profileId ?? undefined,
    }),

  patchApplication: (
    applicationId: string,
    payload: Partial<
      Pick<
        ApplicationRecord,
        | "stage"
        | "priority"
        | "excitement"
        | "salary_range"
        | "deadline"
        | "next_action_at"
        | "notes"
        | "missing_answers_count"
      >
    >,
    profileId?: string | null,
  ) =>
    apiFetch<ApplicationRecord>(`/applications/${encodeURIComponent(applicationId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
      profileId: profileId ?? undefined,
    }),

  createApplication: (payload: {
    job_id: string;
    profile_id?: string | null;
    resume_session_id?: string | null;
    notes?: string;
    force_new?: boolean;
  }) =>
    apiFetch<ApplicationRecord>("/applications", {
      method: "POST",
      body: JSON.stringify(payload),
      profileId: payload.profile_id ?? undefined,
    }),

  scoreApplication: (applicationId: string, profileId?: string | null) =>
    apiFetch<ApplicationScoreResponse>(
      `/applications/${encodeURIComponent(applicationId)}/score`,
      {
        method: "POST",
        body: JSON.stringify({ profile_id: profileId ?? null }),
        profileId: profileId ?? undefined,
      },
    ),

  transitionApplication: (
    applicationId: string,
    status: ApplicationStatus,
    notes?: string,
    profileId?: string | null,
  ) =>
    apiFetch<ApplicationRecord>(
      `/applications/${encodeURIComponent(applicationId)}/transition`,
      {
        method: "POST",
        body: JSON.stringify({ status, notes }),
        profileId: profileId ?? undefined,
      },
    ),

  createApplicationEvent: (
    applicationId: string,
    payload: {
      kind: string;
      label: string;
      detail?: string;
      payload?: Record<string, unknown>;
    },
    profileId?: string | null,
  ) =>
    apiFetch<ApplicationEvent>(
      `/applications/${encodeURIComponent(applicationId)}/events`,
      {
        method: "POST",
        body: JSON.stringify(payload),
        profileId: profileId ?? undefined,
      },
    ),

  createApplicationTask: (
    applicationId: string,
    payload: {
      title: string;
      category?: ApplicationTask["category"];
      due_at?: string | null;
      notes?: string;
    },
    profileId?: string | null,
  ) =>
    apiFetch<ApplicationTask>(
      `/applications/${encodeURIComponent(applicationId)}/tasks`,
      {
        method: "POST",
        body: JSON.stringify(payload),
        profileId: profileId ?? undefined,
      },
    ),

  getLatestApplicationArtifact: (
    applicationId: string,
    type = "tailored_resume",
    status: ApplicationArtifactStatus = "approved",
    profileId?: string | null,
  ) =>
    apiFetch<ApplicationArtifact>(
      `/applications/${encodeURIComponent(applicationId)}/artifacts/latest?type=${encodeURIComponent(type)}&status=${encodeURIComponent(status)}`,
      { profileId: profileId ?? undefined },
    ),

  updateApplicationArtifactStatus: (
    applicationId: string,
    artifactId: string,
    status: ApplicationArtifactStatus,
    profileId?: string | null,
  ) =>
    apiFetch<ApplicationArtifact>(
      `/applications/${encodeURIComponent(applicationId)}/artifacts/${encodeURIComponent(artifactId)}/status`,
      {
        method: "POST",
        body: JSON.stringify({ status }),
        profileId: profileId ?? undefined,
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

  rankTailorProjects: (sessionId: string) =>
    apiFetch<ProjectRankResponse>(
      `/tailor/sessions/${encodeURIComponent(sessionId)}/projects/rank`,
      {
        method: "POST",
      },
    ),

  updateTailorProjects: (sessionId: string, selectedProjectIds: string[]) =>
    apiFetch<ProjectRankResponse>(
      `/tailor/sessions/${encodeURIComponent(sessionId)}/projects`,
      {
        method: "PATCH",
        body: JSON.stringify({ selected_project_ids: selectedProjectIds }),
      },
    ),

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

  approveTailorSession: (
    sessionId: string,
    payload?: { application_id?: string | null; filename?: string | null },
  ) =>
    apiFetch<ApplicationArtifact>(
      `/tailor/sessions/${encodeURIComponent(sessionId)}/approve`,
      {
        method: "POST",
        body: JSON.stringify(payload ?? {}),
      },
    ),
};
