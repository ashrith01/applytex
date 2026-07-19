"use client";

import Link from "next/link";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { CheckCircle2, FileUp, Pencil, ShieldAlert } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth/profile-context";
import { cn } from "@/lib/utils";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { roleLabel } from "@/lib/utils";
import {
  ApplicationQuestionsEditor,
  ApplicationQuestionsView,
} from "@/components/profile/application-questions";
import type {
  EducationProfile,
  ProfilePatch,
  ProfileView,
  WorkExperienceProfile,
} from "@/lib/api/types";

const sections = [
  { id: "personal", label: "Personal & Links" },
  { id: "education", label: "Education" },
  { id: "work", label: "Work Experience" },
  { id: "skills", label: "Skills" },
  { id: "questions", label: "Authorization, EEO & Answers" },
  { id: "preferences", label: "Search Preferences" },
] as const;

type SectionId = (typeof sections)[number]["id"];
type TargetRoleValue = ProfileView["search_preferences"]["target_roles"][number];
type EmploymentTypeValue = ProfileView["search_preferences"]["accepted_employment_types"][number];

const targetRoleOptions: TargetRoleValue[] = [
  "ai_intern",
  "ml_intern",
  "nlp_intern",
  "agentic_ai_intern",
  "data_science_intern",
  "ai_engineer",
  "ml_engineer",
  "data_scientist",
];

const employmentTypeOptions: EmploymentTypeValue[] = ["internship", "full_time"];

export default function ProfilePage() {
  const { profileId, refresh } = useAuth();
  const qc = useQueryClient();
  const [editing, setEditing] = useState<SectionId | null>(null);
  const [draft, setDraft] = useState<ProfileView | null>(null);

  const profile = useQuery({
    queryKey: ["profile", profileId],
    queryFn: () => api.getProfileView(profileId!),
    enabled: !!profileId,
  });

  const setup = useQuery({
    queryKey: ["profile-setup", profileId],
    queryFn: () => api.getProfileSetup(profileId!),
    enabled: !!profileId,
  });

  const save = useMutation({
    mutationFn: (body: ProfileView) => api.patchProfile(profileId!, profileViewToPatch(body)),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["profile", profileId] });
      await qc.invalidateQueries({ queryKey: ["profile-setup", profileId] });
      await refresh();
      setEditing(null);
      setDraft(null);
    },
  });

  const p = profile.data;

  function openEditor(section: SectionId) {
    if (!p) return;
    setEditing(section);
    setDraft(structuredClone(p));
  }

  function closeEditor() {
    setEditing(null);
    setDraft(null);
  }

  function scrollToSection(sectionId: string) {
    document.getElementById(sectionId)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  useEffect(() => {
    if (editing) {
      scrollToSection(editing);
    }
  }, [editing]);

  if (!p) return <p className="text-ink-muted">Loading profile…</p>;

  const answeredQuestions = setup.data?.questions.filter((question) => question.value_present).length ?? 0;
  const totalQuestions = setup.data?.questions.length ?? 0;
  const completion = totalQuestions ? Math.round((answeredQuestions / totalQuestions) * 100) : 0;

  const sectionEditorProps = {
    editing,
    draft,
    setDraft,
    onSave: () => draft && save.mutate(draft),
    onCancel: closeEditor,
    saving: save.isPending,
    saveError: save.isError ? (save.error as Error).message : null,
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-3xl font-extrabold">Profile</h1>
          <p className="mt-2 text-sm text-ink-muted">
            Signed in as @{p.profile_id}. These facts power resume tailoring, reviewed autofill, and reusable ATS answers.
          </p>
        </div>
        <Link href="/profile/resume">
          <Button variant="secondary">
            <FileUp className="mr-2 h-4 w-4" />
            Resume upload
          </Button>
        </Link>
      </div>

      <section className="grid gap-3 md:grid-cols-3">
        <Card className="p-4">
          <p className="font-mono text-xs uppercase tracking-[0.12em] text-ink-muted">autofill completion</p>
          <p className="mt-1 text-3xl font-black">{completion}%</p>
          <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-surface-muted">
            <div className="h-full rounded-full bg-primary" style={{ width: `${completion}%` }} />
          </div>
        </Card>
        <Card className="p-4">
          <p className="flex items-center gap-2 font-mono text-xs uppercase tracking-[0.12em] text-ink-muted">
            <CheckCircle2 className="h-3.5 w-3.5" />
            answered
          </p>
          <p className="mt-1 text-3xl font-black">{answeredQuestions}/{totalQuestions || "—"}</p>
        </Card>
        <Card className="p-4">
          <p className="flex items-center gap-2 font-mono text-xs uppercase tracking-[0.12em] text-ink-muted">
            <ShieldAlert className="h-3.5 w-3.5" />
            missing required
          </p>
          <p className="mt-1 text-3xl font-black">{setup.data?.missing_required.length ?? 0}</p>
        </Card>
      </section>

      {!setup.data?.ready_for_basic_autofill && (
        <Card className="border-amber-200 bg-amber-50/60">
          <CardTitle>Complete required fields</CardTitle>
          <CardDescription className="mt-2">
            Missing: {setup.data?.missing_required.join(", ") || "loading…"}
          </CardDescription>
          <div className="mt-3 flex flex-wrap gap-2">
            {(setup.data?.missing_required ?? []).slice(0, 8).map((item) => (
              <Badge key={item} tone="warning">{item}</Badge>
            ))}
          </div>
        </Card>
      )}

      <nav className="sticky top-0 z-20 -mx-2 flex gap-2 overflow-x-auto border-b border-border bg-white/95 px-2 py-3 backdrop-blur supports-[backdrop-filter]:bg-white/90">
        {sections.map((s) => (
          <a
            key={s.id}
            href={`#${s.id}`}
            onClick={(event) => {
              event.preventDefault();
              scrollToSection(s.id);
            }}
            className={cn(
              "whitespace-nowrap rounded-full px-3 py-1.5 text-sm font-bold transition",
              editing === s.id
                ? "bg-primary/15 text-ink ring-1 ring-primary/30"
                : "text-ink-muted hover:bg-surface-muted hover:text-ink",
            )}
          >
            {s.label}
          </a>
        ))}
      </nav>

      <Section
        id="personal"
        title="Personal & Links"
        section="personal"
        onEdit={() => openEditor("personal")}
        {...sectionEditorProps}
      >
        <div className="space-y-4">
          <div>
            <p className="text-2xl font-extrabold">{p.full_name || "No name yet"}</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Badge tone="muted">{p.email || "Email not set"}</Badge>
              <Badge tone="muted">{p.phone || "Phone not set"}</Badge>
              <Badge tone="muted">{p.location || "Location not set"}</Badge>
            </div>
          </div>
          <InfoGrid
            items={[
              ["First name", p.first_name],
              ["Last name", p.last_name],
              ["LinkedIn", p.linkedin_url],
              ["GitHub", p.github_url],
              ["Portfolio", p.portfolio_url],
              ["Address line 1", p.address.line1],
              ["Address line 2", p.address.line2],
              ["City", p.address.city],
              ["County", p.address.county],
              ["State", p.address.state],
              ["Postal code", p.address.postal_code],
              ["Country", p.address.country],
              ["Resume", p.resume_filename || p.resume_pdf_filename],
              ["Resume source", p.has_latex_source ? "LaTeX stored" : "No LaTeX source"],
              ["Resume PDF", p.has_pdf ? "PDF stored" : "No PDF stored"],
            ]}
          />
        </div>
      </Section>

      <Section
        id="education"
        title="Education"
        section="education"
        onEdit={() => openEditor("education")}
        {...sectionEditorProps}
      >
        <EducationList educations={p.educations?.length ? p.educations : [p.education]} />
      </Section>

      <Section
        id="work"
        title="Work Experience"
        section="work"
        onEdit={() => openEditor("work")}
        {...sectionEditorProps}
      >
        {p.work_experiences.length === 0 ? (
          <p className="text-ink-muted">No work entries yet.</p>
        ) : (
          <WorkExperienceList workExperiences={p.work_experiences} />
        )}
      </Section>

      <Section
        id="skills"
        title="Skills"
        section="skills"
        onEdit={() => openEditor("skills")}
        {...sectionEditorProps}
      >
        <ChipList values={p.skills} empty="No skills listed." />
      </Section>

      <Section
        id="questions"
        title="Authorization, EEO & Reusable Answers"
        section="questions"
        onEdit={() => openEditor("questions")}
        {...sectionEditorProps}
      >
        <ApplicationQuestionsView profile={p} />
      </Section>

      <Section
        id="preferences"
        title="Search Preferences"
        section="preferences"
        onEdit={() => openEditor("preferences")}
        {...sectionEditorProps}
      >
        <div className="space-y-4">
          <InfoGrid
            items={[
              ["Target roles", p.search_preferences.target_roles.map(roleLabel).join(", ")],
              ["Preferred locations", p.search_preferences.preferred_locations.join(", ")],
              ["Remote US", boolLabel(p.search_preferences.allow_remote_us)],
              ["Hybrid", boolLabel(p.search_preferences.allow_hybrid)],
              ["Onsite", boolLabel(p.search_preferences.allow_onsite)],
              ["Willing to relocate", boolLabel(p.search_preferences.willing_to_relocate)],
              ["Employment types", p.search_preferences.accepted_employment_types.join(", ")],
              ["Prioritize internships", boolLabel(p.search_preferences.prioritize_internships)],
              ["Excluded title terms", p.search_preferences.excluded_title_terms.join(", ")],
            ]}
          />
          {Object.keys(p.custom_answers).length > 0 && (
            <div>
              <p className="text-sm font-bold">Custom answers</p>
              <InfoGrid items={Object.entries(p.custom_answers)} />
            </div>
          )}
        </div>
      </Section>
    </div>
  );
}

type SectionEditorProps = {
  section: SectionId;
  editing: SectionId | null;
  draft: ProfileView | null;
  setDraft: (p: ProfileView) => void;
  onSave: () => void;
  onCancel: () => void;
  saving: boolean;
  saveError: string | null;
};

function Section({
  id,
  title,
  section,
  children,
  onEdit,
  editing,
  draft,
  setDraft,
  onSave,
  onCancel,
  saving,
  saveError,
}: {
  id: string;
  title: string;
  children: React.ReactNode;
  onEdit: () => void;
} & SectionEditorProps) {
  const isEditing = editing === section;

  return (
    <Card
      id={id}
      className={cn(
        "scroll-mt-28 transition-shadow",
        isEditing && "relative z-10 border-primary/40 shadow-md ring-1 ring-primary/20",
      )}
    >
      <div className="flex items-center justify-between gap-4">
        <CardTitle>{title}</CardTitle>
        {!isEditing && (
          <Button size="sm" variant="secondary" onClick={onEdit} title={`Edit ${title}`}>
            <Pencil className="h-4 w-4" />
            <span className="sr-only">Edit {title}</span>
          </Button>
        )}
      </div>
      {isEditing && draft ? (
        <div className="mt-4">
          <ProfileEditor section={section} draft={draft} setDraft={setDraft} />
          <div className="mt-4 flex flex-wrap gap-3 border-t border-border pt-4">
            <Button onClick={onSave} disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </Button>
            <Button variant="ghost" onClick={onCancel} disabled={saving}>
              Cancel
            </Button>
          </div>
          {saveError && <p className="mt-2 text-sm text-red-600">{saveError}</p>}
        </div>
      ) : (
        <div className="mt-4 space-y-2">{children}</div>
      )}
    </Card>
  );
}

function InfoGrid({ items }: { items: [string, string | number | boolean | null | undefined][] }) {
  return (
    <dl className="grid gap-x-8 gap-y-4 md:grid-cols-2 xl:grid-cols-3">
      {items.map(([label, value]) => (
        <div key={label} className="min-w-0">
          <dt className="text-xs font-bold uppercase tracking-wide text-ink-muted">{label}</dt>
          <dd className="mt-1 break-words text-sm font-semibold text-ink">{displayValue(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function ChipList({ values, empty }: { values: string[]; empty: string }) {
  if (values.length === 0) return <p className="text-sm text-ink-muted">{empty}</p>;
  return (
    <div className="flex flex-wrap gap-2">
      {values.map((value) => (
        <Badge key={value} tone="muted">{value}</Badge>
      ))}
    </div>
  );
}

function EducationList({ educations }: { educations: EducationProfile[] }) {
  return (
    <div className="space-y-4">
      {educations.map((edu, i) => (
        <div key={`${edu.school}-${i}`} className="border-l-2 border-primary/30 pl-4">
          <div className="text-xs font-semibold text-ink-muted">
            {formatDateRange(edu.start_date, edu.end_date)}
          </div>
          <p className="mt-1 font-bold">{edu.school || "School"}</p>
          <p className="text-sm text-ink-muted">
            {formatEducationCredential(edu)}
          </p>
          {edu.gpa && <p className="text-sm text-ink-muted">GPA: {edu.gpa}</p>}
        </div>
      ))}
    </div>
  );
}

function WorkExperienceList({ workExperiences }: { workExperiences: WorkExperienceProfile[] }) {
  return (
    <div className="space-y-5">
      {workExperiences.map((work, i) => (
        <div key={`${work.company}-${work.job_title}-${i}`} className="border-l-2 border-primary/30 pl-4">
          <div className="text-xs font-semibold text-ink-muted">
            {formatDateRange(work.start_date, work.end_date)}
            {work.location ? ` · ${work.location}` : ""}
          </div>
          <p className="mt-1 font-bold">{work.company || "Company"}</p>
          <p className="text-sm text-ink-muted">{work.job_title || "Role"}</p>
          {work.bullets.length > 0 && (
            <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-ink">
              {work.bullets.map((bullet, index) => (
                <li key={index}>{bullet}</li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}

function displayValue(value: string | number | boolean | null | undefined) {
  if (typeof value === "boolean") return boolLabel(value);
  if (value === null || value === undefined || value === "") return "Not set";
  return String(value);
}

function boolLabel(value: boolean | null | undefined) {
  if (value === true) return "Yes";
  if (value === false) return "No";
  return "Not answered";
}

function BooleanSelect({
  label,
  value,
  onChange,
}: {
  label: string;
  value: boolean | null | undefined;
  onChange: (value: boolean | null) => void;
}) {
  return (
    <label className="text-sm font-semibold">
      {label}
      <select
        className="mt-1 w-full rounded-xl border border-border px-3 py-2 text-sm"
        value={value === true ? "yes" : value === false ? "no" : "unknown"}
        onChange={(e) => {
          const next = e.target.value === "unknown" ? null : e.target.value === "yes";
          onChange(next);
        }}
      >
        <option value="unknown">Not answered</option>
        <option value="yes">Yes</option>
        <option value="no">No</option>
      </select>
    </label>
  );
}

function formatDateRange(startDate: string, endDate: string) {
  if (startDate && endDate) return `${startDate} – ${endDate}`;
  return startDate || endDate || "Dates not set";
}

function formatEducationCredential(edu: EducationProfile) {
  if (edu.degree && edu.major && !edu.degree.toLowerCase().includes(edu.major.toLowerCase())) {
    return `${edu.degree} in ${edu.major}`;
  }
  return edu.degree || edu.major || "Degree";
}

function profileViewToPatch(profile: ProfileView): ProfilePatch {
  const {
    profile_id: _profileId,
    resume_filename: _resumeFilename,
    resume_pdf_filename: _resumePdfFilename,
    has_latex_source: _hasLatexSource,
    has_pdf: _hasPdf,
    resume_updated_at: _resumeUpdatedAt,
    updated_at: _updatedAt,
    ...patch
  } = profile;

  const rawOrientations = patch.equal_opportunity?.sexual_orientation as
    | string[]
    | string
    | null
    | undefined;
  const sexual_orientation = Array.isArray(rawOrientations)
    ? rawOrientations
    : typeof rawOrientations === "string" && rawOrientations.trim()
      ? rawOrientations.split(",").map((item) => item.trim()).filter(Boolean)
      : [];

  return {
    ...patch,
    equal_opportunity: patch.equal_opportunity
      ? {
          ...patch.equal_opportunity,
          sexual_orientation,
          pronouns: patch.equal_opportunity.pronouns ?? null,
        }
      : undefined,
  };
}

function ProfileEditor({
  section,
  draft,
  setDraft,
}: {
  section: SectionId;
  draft: ProfileView;
  setDraft: (p: ProfileView) => void;
}) {
  const inputClass = "mt-1 w-full rounded-xl border border-border px-3 py-2 text-sm";
  const textareaClass = `${inputClass} min-h-[120px]`;

  if (section === "personal") {
    return (
      <div className="mt-4 grid gap-3 md:grid-cols-2">
        {(["full_name", "first_name", "last_name", "email", "phone", "location", "linkedin_url", "github_url", "portfolio_url"] as const).map((key) => (
          <label key={key} className="text-sm font-semibold capitalize">
            {key.replace(/_/g, " ")}
            <input
              className={inputClass}
              value={draft[key]}
              onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
            />
          </label>
        ))}
        {(["line1", "line2", "city", "county", "state", "postal_code", "country"] as const).map((key) => (
          <label key={key} className="text-sm font-semibold capitalize">
            {key.replace(/_/g, " ")}
            <input
              className={inputClass}
              value={draft.address[key]}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  address: {
                    ...draft.address,
                    [key]: e.target.value,
                  },
                })
              }
            />
          </label>
        ))}
      </div>
    );
  }

  if (section === "skills") {
    return (
      <textarea
        className={`${inputClass} mt-4 min-h-[160px]`}
        value={draft.skills.join("\n")}
        onChange={(e) =>
          setDraft({
            ...draft,
            skills: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean),
          })
        }
      />
    );
  }

  if (section === "education") {
    const educations = draft.educations.length ? draft.educations : [draft.education];
    const updateEducation = (index: number, next: EducationProfile) => {
      const updated = educations.map((edu, i) => (i === index ? next : edu));
      setDraft({ ...draft, education: updated[0], educations: updated });
    };
    return (
      <div className="mt-4 space-y-6">
        {educations.map((edu, index) => (
          <div key={index} className="rounded-xl border border-border p-4">
            <div className="mb-3 text-sm font-bold text-ink">Education {index + 1}</div>
            <div className="grid gap-3 md:grid-cols-2">
              {(["school", "degree", "major", "gpa", "start_date", "end_date"] as const).map((key) => (
                <label key={key} className="text-sm font-semibold capitalize">
                  {key.replace(/_/g, " ")}
                  <input
                    className={inputClass}
                    value={edu[key]}
                    onChange={(e) => updateEducation(index, { ...edu, [key]: e.target.value })}
                  />
                </label>
              ))}
            </div>
          </div>
        ))}
        <Button
          size="sm"
          variant="secondary"
          onClick={() => {
            const updated = [...educations, emptyEducation()];
            setDraft({ ...draft, education: updated[0], educations: updated });
          }}
        >
          Add education
        </Button>
      </div>
    );
  }

  if (section === "work") {
    const workExperiences = draft.work_experiences.length ? draft.work_experiences : [emptyWorkExperience()];
    const updateWork = (index: number, next: WorkExperienceProfile) => {
      setDraft({
        ...draft,
        work_experiences: workExperiences.map((work, i) => (i === index ? next : work)),
      });
    };
    return (
      <div className="mt-4 space-y-6">
        {workExperiences.map((work, index) => (
          <div key={index} className="rounded-xl border border-border p-4">
            <div className="mb-3 text-sm font-bold text-ink">Work experience {index + 1}</div>
            <div className="grid gap-3 md:grid-cols-2">
              {(["company", "job_title", "job_type", "location", "start_date", "end_date"] as const).map((key) => (
                <label key={key} className="text-sm font-semibold capitalize">
                  {key.replace(/_/g, " ")}
                  <input
                    className={inputClass}
                    value={work[key]}
                    onChange={(e) => updateWork(index, { ...work, [key]: e.target.value })}
                  />
                </label>
              ))}
              <label className="text-sm font-semibold md:col-span-2">
                Bullets
                <textarea
                  className={textareaClass}
                  value={work.bullets.join("\n")}
                  onChange={(e) => {
                    const bullets = e.target.value.split("\n").map((s) => s.trim()).filter(Boolean);
                    updateWork(index, {
                      ...work,
                      bullets,
                      summary: bullets[0] ?? "",
                    });
                  }}
                />
              </label>
            </div>
          </div>
        ))}
        <Button
          size="sm"
          variant="secondary"
          onClick={() => setDraft({ ...draft, work_experiences: [...workExperiences, emptyWorkExperience()] })}
        >
          Add work experience
        </Button>
      </div>
    );
  }

  if (section === "questions") {
    return <ApplicationQuestionsEditor draft={draft} setDraft={setDraft} />;
  }

  const updateSearchPreferences = (next: Partial<ProfileView["search_preferences"]>) => {
    setDraft({
      ...draft,
      search_preferences: {
        ...draft.search_preferences,
        ...next,
      },
    });
  };

  return (
    <div className="mt-4 space-y-5">
      <div>
        <p className="text-sm font-semibold">Target roles</p>
        <div className="mt-2 grid gap-2 md:grid-cols-2">
          {targetRoleOptions.map((role) => (
            <label key={role} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={draft.search_preferences.target_roles.includes(role)}
                onChange={(e) => {
                  const roles = e.target.checked
                    ? [...draft.search_preferences.target_roles, role]
                    : draft.search_preferences.target_roles.filter((item) => item !== role);
                  updateSearchPreferences({ target_roles: roles });
                }}
              />
              {roleLabel(role)}
            </label>
          ))}
        </div>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="text-sm font-semibold">
          Preferred locations
          <textarea
            className={`${inputClass} min-h-[110px]`}
            value={draft.search_preferences.preferred_locations.join("\n")}
            onChange={(e) =>
              updateSearchPreferences({
                preferred_locations: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean),
              })
            }
            placeholder="One location per line"
          />
        </label>
        <label className="text-sm font-semibold">
          Excluded title terms
          <textarea
            className={`${inputClass} min-h-[110px]`}
            value={draft.search_preferences.excluded_title_terms.join("\n")}
            onChange={(e) =>
              updateSearchPreferences({
                excluded_title_terms: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean),
              })
            }
            placeholder="One title term per line"
          />
        </label>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <BooleanSelect
          label="Allow remote US"
          value={draft.search_preferences.allow_remote_us}
          onChange={(value) => updateSearchPreferences({ allow_remote_us: value === true })}
        />
        <BooleanSelect
          label="Allow hybrid"
          value={draft.search_preferences.allow_hybrid}
          onChange={(value) => updateSearchPreferences({ allow_hybrid: value === true })}
        />
        <BooleanSelect
          label="Allow onsite"
          value={draft.search_preferences.allow_onsite}
          onChange={(value) => updateSearchPreferences({ allow_onsite: value === true })}
        />
        <BooleanSelect
          label="Willing to relocate"
          value={draft.search_preferences.willing_to_relocate}
          onChange={(value) => updateSearchPreferences({ willing_to_relocate: value === true })}
        />
        <BooleanSelect
          label="Prioritize internships"
          value={draft.search_preferences.prioritize_internships}
          onChange={(value) => updateSearchPreferences({ prioritize_internships: value === true })}
        />
      </div>
      <div>
        <p className="text-sm font-semibold">Accepted employment types</p>
        <div className="mt-2 flex flex-wrap gap-4">
          {employmentTypeOptions.map((type) => (
            <label key={type} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={draft.search_preferences.accepted_employment_types.includes(type)}
                onChange={(e) => {
                  const types = e.target.checked
                    ? [...draft.search_preferences.accepted_employment_types, type]
                    : draft.search_preferences.accepted_employment_types.filter((item) => item !== type);
                  updateSearchPreferences({ accepted_employment_types: types });
                }}
              />
              {type === "full_time" ? "Full-time" : "Internship"}
            </label>
          ))}
        </div>
      </div>
    </div>
  );
}

function emptyEducation(): EducationProfile {
  return {
    school: "",
    degree: "",
    degree_level: "",
    major: "",
    field_of_study_candidates: [],
    start_date: "",
    end_date: "",
    currently_studying: false,
    graduation_month: "",
    graduation_year: "",
    gpa: "",
  };
}

function emptyWorkExperience(): WorkExperienceProfile {
  return {
    job_title: "",
    company: "",
    job_type: "",
    location: "",
    start_date: "",
    end_date: "",
    currently_working: false,
    summary: "",
    bullets: [],
  };
}
