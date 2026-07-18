"use client";

import type {
  ApplicationFactsProfile,
  CompanyRelationshipProfile,
  CompensationPreference,
  EqualOpportunityProfile,
  ProfileView,
  WorkAuthorizationProfile,
} from "@/lib/api/types";
import {
  GENDER_OPTIONS,
  PRONOUNS_OPTIONS,
  RACE_OPTIONS,
  SEXUAL_ORIENTATION_OPTIONS,
  YES_NO_DECLINE_OPTIONS,
  YES_NO_OPTIONS,
} from "@/lib/profile-application-questions";

const selectClass = "mt-1 w-full rounded-xl border border-border px-3 py-2 text-sm";

type ReusableAnswerField = {
  key: string;
  label: string;
  placeholder: string;
  options?: readonly string[];
  multiline?: boolean;
};

const REUSABLE_ANSWER_FIELDS: readonly ReusableAnswerField[] = [
  { key: "Phone device type", label: "Phone device type", placeholder: "Mobile", options: ["Mobile", "Home", "Work"] },
  { key: "Earliest start date", label: "Earliest available start date", placeholder: "Immediately or YYYY-MM-DD" },
  { key: "Reliable commute", label: "Can reliably commute", placeholder: "Select an answer", options: YES_NO_OPTIONS },
  { key: "Previously employed by company", label: "Previously employed by the company", placeholder: "Select an answer", options: YES_NO_OPTIONS },
  {
    key: "Bound by restrictive agreements",
    label: "Bound by non-compete / NDA / other restrictive agreements",
    placeholder: "Select an answer",
    options: YES_NO_OPTIONS,
  },
  { key: "Security clearance", label: "Active security clearance", placeholder: "Select an answer", options: YES_NO_OPTIONS },
  { key: "Available to work weekends", label: "Available to work weekends", placeholder: "Select an answer", options: YES_NO_OPTIONS },
  { key: "Onsite availability", label: "Able to work onsite or hybrid", placeholder: "Select an answer", options: YES_NO_OPTIONS },
  { key: "SMS consent", label: "Consent to application SMS updates", placeholder: "Select an answer", options: YES_NO_OPTIONS },
  { key: "Relevant project link", label: "Most relevant project link", placeholder: "https://…" },
  { key: "How did you hear about us?", label: "How did you hear about the company?", placeholder: "Job board, referral, event…" },
  { key: "Why this role", label: "Why are you interested in this role?", placeholder: "Reusable evidence-based response", multiline: true },
  { key: "Production AI system summary", label: "Production system or project summary", placeholder: "A concise example you can defend", multiline: true },
  { key: "Cover letter", label: "Reusable short cover letter", placeholder: "Optional reusable draft", multiline: true },
  { key: "Additional application context", label: "Anything else applications should know?", placeholder: "Optional context such as availability or relocation constraints", multiline: true },
];

function displayAnswer(value: string | null | undefined) {
  return value && value.trim() ? value : "Not answered";
}

function boolToYesNo(value: boolean | null | undefined) {
  if (value === true) return "Yes";
  if (value === false) return "No";
  return "Not answered";
}

function QuestionRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-border/70 bg-surface-muted/40 px-4 py-3">
      <p className="text-sm font-semibold text-ink">{label}</p>
      <p className="mt-1 text-sm text-ink-muted">{value}</p>
    </div>
  );
}

export function ApplicationQuestionsView({ profile }: { profile: ProfileView }) {
  const eeo = normalizeEqualOpportunity(profile.equal_opportunity);
  const auth = profile.work_authorization;
  const facts = profile.application_facts;
  const orientations =
    eeo.sexual_orientation.length > 0 ? eeo.sexual_orientation.join(", ") : "Not answered";
  const hasVoluntary = hasVoluntaryEeoAnswers(eeo);
  const autofillBlocked = hasVoluntary && !eeo.allow_autofill;

  return (
    <div className="space-y-6">
      <div>
        <p className="mb-2 text-xs font-bold uppercase tracking-[0.16em] text-ink-muted">Core eligibility</p>
        <div className="space-y-3">
          <QuestionRow
            label="Where are you currently located?"
            value={displayAnswer(profile.location)}
          />
          <QuestionRow
            label="Are you authorized to work in the US?"
            value={boolToYesNo(auth.authorized_to_work_in_us)}
          />
          <QuestionRow
            label="Do you currently require employment visa sponsorship?"
            value={boolToYesNo(auth.current_requires_sponsorship)}
          />
          <QuestionRow
            label="Will you require employment visa sponsorship in the future?"
            value={boolToYesNo(auth.future_requires_sponsorship)}
          />
          <QuestionRow label="Are you at least 18 years old?" value={boolToYesNo(facts.is_at_least_18)} />
          <QuestionRow label="Are you willing to relocate?" value={boolToYesNo(facts.willing_to_relocate)} />
          <QuestionRow label="Are you willing to travel?" value={boolToYesNo(facts.willing_to_travel)} />
          <QuestionRow
            label="Do you have an active non-compete or non-solicit?"
            value={boolToYesNo(facts.active_non_compete_or_non_solicit)}
          />
        </div>
      </div>

      <div>
        <p className="mb-2 text-xs font-bold uppercase tracking-[0.16em] text-ink-muted">Compensation</p>
        <div className="grid gap-3 md:grid-cols-2">
          {facts.compensation_preferences.map((preference) => (
            <QuestionRow
              key={`${preference.employment_type}-${preference.period}`}
              label={`${preference.employment_type === "any" ? "Default" : preference.employment_type} compensation`}
              value={`${preference.amount} ${preference.currency} ${preference.period}`}
            />
          ))}
          {!facts.compensation_preferences.length && <QuestionRow label="Default compensation" value="Not answered" />}
        </div>
      </div>

      <div>
        <p className="text-xs font-bold uppercase tracking-[0.16em] text-ink-muted">Reusable autofill answers</p>
        <p className="mb-3 mt-1 text-xs text-ink-muted">
          These answers are used only when a form asks a matching question. Empty answers stay unresolved for review.
        </p>
        <div className="grid gap-3 md:grid-cols-2">
          {REUSABLE_ANSWER_FIELDS.map((field) => (
            <QuestionRow
              key={field.key}
              label={field.label}
              value={displayAnswer(profile.custom_answers[field.key])}
            />
          ))}
        </div>
      </div>

      <div>
        <p className="mb-2 text-xs font-bold uppercase tracking-[0.16em] text-ink-muted">Voluntary demographics</p>
      {autofillBlocked && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950">
          You saved voluntary answers, but <strong>EEO autofill is off</strong>. Job forms will skip
          Gender, Disability, Veteran, and similar fields until you enable autofill below and save.
        </div>
      )}
        <div className="space-y-3">
          <QuestionRow label="Do you have a disability?" value={displayAnswer(eeo.disability)} />
          <QuestionRow label="Are you a veteran?" value={displayAnswer(eeo.veteran_status)} />
          <QuestionRow label="What is your gender?" value={displayAnswer(eeo.gender)} />
          <QuestionRow label="Do you identify as LGBTQ+?" value={displayAnswer(eeo.lgbtq)} />
          <QuestionRow label="Are you Hispanic or Latino?" value={displayAnswer(eeo.hispanic_or_latino)} />
          <QuestionRow label="How would you identify your race?" value={displayAnswer(eeo.race)} />
          <QuestionRow
            label="How would you describe your sexual orientation? (mark all that apply)"
            value={orientations}
          />
          <QuestionRow label="What are your pronouns?" value={displayAnswer(eeo.pronouns)} />
        </div>
        <p className="mt-3 text-xs text-ink-muted">
          Voluntary answers are sent only when autofill is enabled. Current setting: {eeo.allow_autofill ? "Enabled" : "Disabled"}.
        </p>
      </div>
    </div>
  );
}

function YesNoSelect({
  label,
  value,
  onChange,
}: {
  label: string;
  value: boolean | null | undefined;
  onChange: (value: boolean | null) => void;
}) {
  return (
    <label className="block text-sm font-semibold">
      {label}
      <select
        className={selectClass}
        value={value === true ? "Yes" : value === false ? "No" : ""}
        onChange={(e) => {
          const next = e.target.value;
          onChange(next === "" ? null : next === "Yes");
        }}
      >
        <option value="">Not answered</option>
        {YES_NO_OPTIONS.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </label>
  );
}

function OptionSelect({
  label,
  value,
  options,
  onChange,
  placeholder = "Not answered",
}: {
  label: string;
  value: string | null | undefined;
  options: readonly string[];
  onChange: (value: string | null) => void;
  placeholder?: string;
}) {
  return (
    <label className="block text-sm font-semibold">
      {label}
      <select
        className={selectClass}
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
      >
        <option value="">{placeholder}</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </label>
  );
}

function SexualOrientationMultiSelect({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string[];
  onChange: (value: string[]) => void;
}) {
  function toggle(option: string) {
    if (value.includes(option)) {
      onChange(value.filter((item) => item !== option));
      return;
    }
    onChange([...value, option]);
  }

  return (
    <fieldset className="rounded-xl border border-border px-4 py-3">
      <legend className="px-1 text-sm font-semibold">{label}</legend>
      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        {SEXUAL_ORIENTATION_OPTIONS.map((option) => (
          <label key={option} className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={value.includes(option)}
              onChange={() => toggle(option)}
            />
            {option}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export function ApplicationQuestionsEditor({
  draft,
  setDraft,
}: {
  draft: ProfileView;
  setDraft: (profile: ProfileView) => void;
}) {
  const eeo = normalizeEqualOpportunity(draft.equal_opportunity);
  const facts = draft.application_facts;
  const updateAuth = (patch: Partial<WorkAuthorizationProfile>) => {
    setDraft({
      ...draft,
      work_authorization: {
        ...draft.work_authorization,
        ...patch,
      },
    });
  };

  const updateEeo = (patch: Partial<EqualOpportunityProfile>) => {
    setDraft({
      ...draft,
      equal_opportunity: normalizeEqualOpportunity({
        ...eeo,
        ...patch,
      }),
    });
  };

  const updateFacts = (patch: Partial<ApplicationFactsProfile>) => {
    setDraft({
      ...draft,
      application_facts: {
        ...facts,
        ...patch,
      },
    });
  };

  const updateCompensation = (
    employmentType: CompensationPreference["employment_type"],
    patch: Partial<CompensationPreference>,
  ) => {
    const current = facts.compensation_preferences.find((item) => !item.application_id && item.employment_type === employmentType) ?? {
      application_id: null,
      employment_type: employmentType,
      amount: "",
      currency: "USD",
      period: "annual" as const,
    };
    const next = { ...current, ...patch };
    const others = facts.compensation_preferences.filter(
      (item) => item.application_id || item.employment_type !== employmentType,
    );
    updateFacts({ compensation_preferences: next.amount ? [...others, next] : others });
  };

  const updateCompanyRelationship = (company: string, patch: Partial<CompanyRelationshipProfile>) => {
    updateFacts({
      company_relationships: {
        ...facts.company_relationships,
        [company]: {
          ...facts.company_relationships[company],
          currently_employed: facts.company_relationships[company]?.currently_employed ?? null,
          employed_by_affiliate: facts.company_relationships[company]?.employed_by_affiliate ?? null,
          previously_employed: facts.company_relationships[company]?.previously_employed ?? null,
          ...patch,
        },
      },
    });
  };

  const updateCustomAnswer = (key: string, value: string) => {
    const customAnswers = { ...draft.custom_answers };
    if (value.trim()) customAnswers[key] = value;
    else delete customAnswers[key];
    setDraft({ ...draft, custom_answers: customAnswers });
  };

  return (
    <div className="space-y-5">
      <div>
        <p className="mb-3 text-xs font-bold uppercase tracking-[0.16em] text-ink-muted">Core eligibility</p>
      <div className="grid gap-4 md:grid-cols-2">
        <label className="block text-sm font-semibold">
          Where are you currently located?
          <input
            className={selectClass}
            value={draft.location}
            onChange={(e) => setDraft({ ...draft, location: e.target.value })}
            placeholder="City, state, and country"
          />
        </label>
        <YesNoSelect
          label="Are you authorized to work in the US?"
          value={draft.work_authorization.authorized_to_work_in_us}
          onChange={(value) => updateAuth({ authorized_to_work_in_us: value })}
        />
        <YesNoSelect
          label="Do you currently require employment visa sponsorship?"
          value={draft.work_authorization.current_requires_sponsorship}
          onChange={(value) => updateAuth({ current_requires_sponsorship: value })}
        />
        <YesNoSelect
          label="Will you require employment visa sponsorship in the future?"
          value={draft.work_authorization.future_requires_sponsorship}
          onChange={(value) => updateAuth({ future_requires_sponsorship: value })}
        />
        <YesNoSelect
          label="Are you at least 18 years old?"
          value={facts.is_at_least_18}
          onChange={(value) => updateFacts({ is_at_least_18: value })}
        />
        <YesNoSelect
          label="Are you willing to relocate?"
          value={facts.willing_to_relocate}
          onChange={(value) => updateFacts({ willing_to_relocate: value })}
        />
        <YesNoSelect
          label="Are you willing to travel?"
          value={facts.willing_to_travel}
          onChange={(value) => updateFacts({ willing_to_travel: value })}
        />
        <YesNoSelect
          label="Do you have an active non-compete or non-solicit?"
          value={facts.active_non_compete_or_non_solicit}
          onChange={(value) => updateFacts({ active_non_compete_or_non_solicit: value })}
        />
      </div>
      </div>

      <div className="rounded-2xl border border-border bg-surface-muted/30 p-4">
        <p className="text-xs font-bold uppercase tracking-[0.16em] text-ink-muted">Compensation defaults</p>
        <div className="mt-3 grid gap-4 md:grid-cols-2">
          {(["internship", "full_time"] as const).map((employmentType) => {
            const preference = facts.compensation_preferences.find(
              (item) => !item.application_id && item.employment_type === employmentType,
            );
            return (
              <div key={employmentType} className="grid grid-cols-[minmax(0,1fr)_110px] gap-2">
                <label className="text-sm font-semibold">
                  {employmentType === "internship" ? "Internship amount" : "Full-time amount"}
                  <input
                    className={selectClass}
                    inputMode="decimal"
                    value={preference?.amount ?? ""}
                    onChange={(event) => updateCompensation(employmentType, { amount: event.target.value })}
                    placeholder="75000"
                  />
                </label>
                <label className="text-sm font-semibold">
                  Period
                  <select
                    className={selectClass}
                    value={preference?.period ?? "annual"}
                    onChange={(event) => updateCompensation(employmentType, {
                      period: event.target.value as CompensationPreference["period"],
                    })}
                  >
                    <option value="hourly">Hourly</option>
                    <option value="monthly">Monthly</option>
                    <option value="annual">Annual</option>
                  </select>
                </label>
              </div>
            );
          })}
        </div>
      </div>

      <div className="rounded-2xl border border-border bg-surface-muted/30 p-4">
        <p className="text-xs font-bold uppercase tracking-[0.16em] text-ink-muted">Company relationships</p>
        <p className="mb-3 mt-1 text-xs text-ink-muted">These answers apply only when the hiring company name matches.</p>
        <div className="space-y-3">
          {Object.entries(facts.company_relationships).map(([company, relationship]) => (
            <div key={company} className="grid gap-3 border-b border-border pb-3 md:grid-cols-3">
              <p className="text-sm font-semibold md:col-span-3">{company}</p>
              <YesNoSelect label="Currently employed" value={relationship.currently_employed} onChange={(value) => updateCompanyRelationship(company, { currently_employed: value })} />
              <YesNoSelect label="Employed by affiliate" value={relationship.employed_by_affiliate} onChange={(value) => updateCompanyRelationship(company, { employed_by_affiliate: value })} />
              <YesNoSelect label="Previously employed" value={relationship.previously_employed} onChange={(value) => updateCompanyRelationship(company, { previously_employed: value })} />
            </div>
          ))}
          {!Object.keys(facts.company_relationships).length && (
            <p className="text-sm text-ink-muted">Company-specific answers can be added from an unresolved application row.</p>
          )}
        </div>
      </div>

      <div className="rounded-2xl border border-border bg-surface-muted/30 p-4">
        <p className="text-xs font-bold uppercase tracking-[0.16em] text-ink-muted">Reusable autofill answers</p>
        <p className="mb-4 mt-1 text-xs text-ink-muted">
          Save only facts and wording you are comfortable reusing. The extension still shows every planned answer before filling.
        </p>
        <div className="grid gap-4 md:grid-cols-2">
          {REUSABLE_ANSWER_FIELDS.map((field) => (
            <label key={field.key} className={`block text-sm font-semibold ${field.multiline ? "md:col-span-2" : ""}`}>
              {field.label}
              {field.options ? (
                <select
                  className={selectClass}
                  value={draft.custom_answers[field.key] ?? ""}
                  onChange={(event) => updateCustomAnswer(field.key, event.target.value)}
                >
                  <option value="">Not answered</option>
                  {field.options.map((option) => (
                    <option key={option} value={option}>{option}</option>
                  ))}
                </select>
              ) : field.multiline ? (
                <textarea
                  className={`${selectClass} min-h-[96px] resize-y`}
                  value={draft.custom_answers[field.key] ?? ""}
                  onChange={(event) => updateCustomAnswer(field.key, event.target.value)}
                  placeholder={field.placeholder}
                />
              ) : (
                <input
                  className={selectClass}
                  value={draft.custom_answers[field.key] ?? ""}
                  onChange={(event) => updateCustomAnswer(field.key, event.target.value)}
                  placeholder={field.placeholder}
                />
              )}
            </label>
          ))}
        </div>
      </div>

      <div>
        <p className="mb-3 text-xs font-bold uppercase tracking-[0.16em] text-ink-muted">Voluntary demographics</p>
        <label className="mb-4 flex items-start gap-3 rounded-xl border border-primary/30 bg-primary/5 px-4 py-3 text-sm">
          <input
            type="checkbox"
            className="mt-1"
            checked={eeo.allow_autofill}
            onChange={(e) => updateEeo({ allow_autofill: e.target.checked })}
          />
          <span>
            <strong>Allow voluntary EEO autofill on job applications.</strong> When off, saved demographic answers remain in your profile but are skipped on application forms.
          </span>
        </label>

      <div className="grid gap-4 md:grid-cols-2">
        <OptionSelect
          label="Do you have a disability?"
          value={eeo.disability}
          options={YES_NO_DECLINE_OPTIONS}
          onChange={(value) => updateEeo({ disability: value })}
        />
        <OptionSelect
          label="Are you a veteran?"
          value={eeo.veteran_status}
          options={YES_NO_DECLINE_OPTIONS}
          onChange={(value) => updateEeo({ veteran_status: value })}
        />
        <OptionSelect
          label="What is your gender?"
          value={eeo.gender}
          options={GENDER_OPTIONS}
          onChange={(value) => updateEeo({ gender: value })}
        />
        <OptionSelect
          label="Do you identify as LGBTQ+?"
          value={eeo.lgbtq}
          options={YES_NO_DECLINE_OPTIONS}
          onChange={(value) => updateEeo({ lgbtq: value })}
        />
        <OptionSelect
          label="Are you Hispanic or Latino?"
          value={eeo.hispanic_or_latino}
          options={YES_NO_DECLINE_OPTIONS}
          onChange={(value) => updateEeo({ hispanic_or_latino: value })}
        />
        <OptionSelect
          label="How would you identify your race?"
          value={eeo.race}
          options={RACE_OPTIONS}
          onChange={(value) => updateEeo({ race: value })}
        />
        <OptionSelect
          label="What are your pronouns?"
          value={eeo.pronouns}
          options={PRONOUNS_OPTIONS}
          onChange={(value) => updateEeo({ pronouns: value })}
        />
      </div>

      <SexualOrientationMultiSelect
        label="How would you describe your sexual orientation? (mark all that apply)"
        value={eeo.sexual_orientation}
        onChange={(value) => updateEeo({ sexual_orientation: value })}
      />
      </div>
    </div>
  );
}

export function hasVoluntaryEeoAnswers(eeo: EqualOpportunityProfile): boolean {
  return Boolean(
    eeo.disability ||
      eeo.gender ||
      eeo.lgbtq ||
      eeo.veteran_status ||
      eeo.race ||
      eeo.hispanic_or_latino ||
      eeo.pronouns ||
      eeo.sexual_orientation.length > 0,
  );
}

export function ensureEeoAutofillEnabled(profile: ProfileView): ProfileView {
  const eeo = normalizeEqualOpportunity(profile.equal_opportunity);
  if (eeo.allow_autofill || !hasVoluntaryEeoAnswers(eeo)) {
    return profile;
  }
  return {
    ...profile,
    equal_opportunity: {
      ...eeo,
      allow_autofill: true,
    },
  };
}

function normalizeEqualOpportunity(eeo: EqualOpportunityProfile): EqualOpportunityProfile {
  const rawOrientations = eeo.sexual_orientation as string[] | string | null | undefined;
  const sexual_orientation = Array.isArray(rawOrientations)
    ? rawOrientations
    : typeof rawOrientations === "string" && rawOrientations.trim()
      ? rawOrientations.split(",").map((item) => item.trim()).filter(Boolean)
      : [];
  return {
    ...eeo,
    sexual_orientation,
    pronouns: eeo.pronouns ?? null,
  };
}
