export type ApplicationStatus =
  | "discovered"
  | "scored"
  | "selected"
  | "resume_ready"
  | "form_scanned"
  | "needs_input"
  | "ready_for_review"
  | "approved"
  | "submitting"
  | "submitted"
  | "blocked"
  | "failed"
  | "skipped";

export type JobProvider = "linkedin" | "greenhouse" | "lever" | "ashby";

export type TargetRole =
  | "ai_intern"
  | "ml_intern"
  | "nlp_intern"
  | "agentic_ai_intern"
  | "data_science_intern"
  | "ai_engineer"
  | "ml_engineer"
  | "data_scientist";

export interface AddressProfile {
  city: string;
  state: string;
  postal_code: string;
  country: string;
}

export interface EducationProfile {
  school: string;
  degree: string;
  major: string;
  start_date: string;
  end_date: string;
  currently_studying: boolean;
  graduation_month: string;
  graduation_year: string;
  gpa: string;
}

export interface WorkExperienceProfile {
  job_title: string;
  company: string;
  job_type: string;
  location: string;
  start_date: string;
  end_date: string;
  currently_working: boolean;
  summary: string;
  bullets: string[];
}

export interface WorkAuthorizationProfile {
  authorized_to_work_in_us: boolean | null;
  requires_sponsorship: boolean | null;
  internship_requires_sponsorship?: boolean | null;
  full_time_requires_sponsorship?: boolean | null;
}

export interface EqualOpportunityProfile {
  allow_autofill: boolean;
  disability: string | null;
  gender: string | null;
  veteran_status: string | null;
  race: string | null;
  hispanic_or_latino: string | null;
  lgbtq: string | null;
  sexual_orientation: string[];
  pronouns: string | null;
}

export interface SearchPreferences {
  target_roles: TargetRole[];
  preferred_locations: string[];
  allow_remote_us: boolean;
  allow_hybrid: boolean;
  allow_onsite: boolean;
  willing_to_relocate: boolean;
  accepted_employment_types: ("internship" | "full_time")[];
  prioritize_internships: boolean;
  excluded_title_terms: string[];
}

export interface CandidateProfile {
  profile_id: string;
  full_name: string;
  first_name: string;
  last_name: string;
  email: string;
  phone: string;
  location: string;
  address: AddressProfile;
  linkedin_url: string;
  portfolio_url: string;
  github_url: string;
  resume_filename: string;
  resume_latex_source: string;
  resume_pdf_filename: string;
  resume_pdf_b64: string;
  resume_updated_at: string;
  skills: string[];
  education: EducationProfile;
  educations: EducationProfile[];
  work_experiences: WorkExperienceProfile[];
  work_authorization: WorkAuthorizationProfile;
  equal_opportunity: EqualOpportunityProfile;
  search_preferences: SearchPreferences;
  custom_answers: Record<string, string>;
  updated_at: string;
}

export type ProfileView = Omit<CandidateProfile, "resume_latex_source" | "resume_pdf_b64"> & {
  has_latex_source: boolean;
  has_pdf: boolean;
};

export type ProfilePatch = Partial<
  Omit<
    ProfileView,
    | "profile_id"
    | "resume_filename"
    | "resume_pdf_filename"
    | "has_latex_source"
    | "has_pdf"
    | "resume_updated_at"
    | "updated_at"
  >
>;

export interface ActiveProfileResponse {
  profile_id: string;
  full_name: string;
  email: string;
  resume_filename: string;
  has_pdf: boolean;
  has_latex_source: boolean;
}

export interface ProfileSetupQuestion {
  key: string;
  label: string;
  category: string;
  required: boolean;
  value_present: boolean;
}

export interface ProfileSetupResponse {
  questions: ProfileSetupQuestion[];
  missing_required: string[];
  ready_for_basic_autofill: boolean;
}

export interface ProfileResumeInfo {
  profile_id: string;
  resume_filename: string;
  resume_pdf_filename: string;
  has_latex_source: boolean;
  has_pdf: boolean;
  resume_updated_at: string;
}

export interface ProfileResumeUploadResponse extends ProfileResumeInfo {
  prefill_applied: string[];
  prefill_labels: string[];
}

export interface JobPosting {
  job_id: string;
  provider: JobProvider;
  board_token: string;
  external_id: string;
  company: string;
  title: string;
  description: string;
  location: string;
  workplace_type: string;
  source_url: string;
  apply_url: string;
  published_at: string | null;
  retrieved_at: string;
  industry: string | null;
  target_role: TargetRole | null;
  employment_track: string;
  search_score: number;
}

export interface JobSourceConfig {
  provider: JobProvider;
  board_token: string;
  company: string;
  industry?: string | null;
}

export interface JobSearchQuery {
  text: string;
  role_keywords: string[];
  locations: string[];
  remote_only: boolean;
  limit: number;
  target_roles: TargetRole[];
}

export interface SourceSearchError {
  provider: JobProvider;
  board_token: string;
  message: string;
}

export interface JobSearchResult {
  search_id: string;
  query: JobSearchQuery;
  sources: JobSourceConfig[];
  jobs: JobPosting[];
  errors: SourceSearchError[];
  created_at: string;
}

export interface ApplicationRecord {
  application_id: string;
  job_id: string;
  status: ApplicationStatus;
  resume_session_id: string | null;
  notes: string;
  created_at: string;
  updated_at: string;
  approved_at: string | null;
  submitted_at: string | null;
}

export interface AtsSnapshot {
  score: number;
  raw_score?: number;
  required_found: string[];
  required_missing: string[];
  preferred_found: string[];
  preferred_missing: string[];
  keyword_hits: string[];
  keyword_misses: string[];
  excluded_unconfirmed_skills?: string[];
  submission_blockers?: string[];
}

export interface AnalyzeResponse {
  job_keywords: Record<string, unknown>;
  baseline_ats: AtsSnapshot;
  screening: Record<string, unknown>;
  skill_candidates: string[];
  theme_gaps: string[];
  skill_groups: Record<string, string[]>;
  editable_statement_count: number;
  latency_ms: Record<string, number>;
}

export interface UploadResponse {
  session_id: string;
  filename: string;
  editable: Record<string, unknown>;
  resume_data: Record<string, unknown>;
  page_budget: Record<string, unknown>;
}

export interface StatementDiff {
  stmt_id: string;
  original?: string;
  value?: string;
  reason?: string;
}

export interface OptimizeResponse {
  session_id: string;
  optimization_strategy: string;
  reviewer_backend: string;
  strategy_notes: string;
  diff: StatementDiff[];
  warnings: string[];
  ats_target_score: number;
  ats_target_met: boolean;
  confirmed_skills: string[];
  confirmation_required_skills: string[];
  ats_before: AtsSnapshot | null;
  ats_after: AtsSnapshot | null;
  overflow: boolean;
  visual_overflow: boolean;
  min_text_baseline_pt: number | null;
  page_count: number;
  modified_latex: string;
  modified_pdf_b64: string | null;
}

export interface StatusResponse {
  session_id: string;
  filename: string;
  optimized: boolean;
  overflow: boolean | null;
  visual_overflow: boolean | null;
  page_count: number | null;
  ats_target_met: boolean | null;
  ats_score: number | null;
  confirmation_required_skills: string[];
  changes_applied: number;
  warnings: string[];
}

export interface ReportResponse {
  run_record: Record<string, unknown> | null;
  optimized: boolean;
}

export interface TailorSessionResponse {
  session_id: string;
  job_id: string;
  profile_id: string;
  application_id: string | null;
  latex_session_id: string | null;
  job: JobPosting;
  match_preview: AnalyzeResponse;
  current_latex: string;
  confirmed_skills: string[];
  diff: StatementDiff[];
  change_history: StatementDiff[];
  last_result: Record<string, unknown> | null;
}

export interface HealthResponse {
  status: string;
  sessions: string;
}
