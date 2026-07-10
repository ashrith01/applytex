"use client";

import type { EqualOpportunityProfile, ProfileView, WorkAuthorizationProfile } from "@/lib/api/types";
import {
  GENDER_OPTIONS,
  PRONOUNS_OPTIONS,
  RACE_OPTIONS,
  SEXUAL_ORIENTATION_OPTIONS,
  YES_NO_DECLINE_OPTIONS,
  YES_NO_OPTIONS,
} from "@/lib/profile-application-questions";

const selectClass = "mt-1 w-full rounded-xl border border-border px-3 py-2 text-sm";

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
  const orientations =
    eeo.sexual_orientation.length > 0 ? eeo.sexual_orientation.join(", ") : "Not answered";
  const hasVoluntary = hasVoluntaryEeoAnswers(eeo);
  const autofillBlocked = hasVoluntary && !eeo.allow_autofill;

  return (
    <div className="space-y-3">
      {autofillBlocked && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950">
          You saved voluntary answers, but <strong>EEO autofill is off</strong>. Job forms will skip
          Gender, Disability, Veteran, and similar fields until you enable autofill below and save.
        </div>
      )}
      <QuestionRow
        label="Are you authorized to work in the US?"
        value={boolToYesNo(auth.authorized_to_work_in_us)}
      />
      <QuestionRow
        label="Will you now or in the future require sponsorship for employment visa status?"
        value={boolToYesNo(auth.requires_sponsorship)}
      />
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
      <p className="text-xs text-ink-muted">
        Voluntary answers (gender, disability, veteran, etc.) are only sent to job forms when autofill
        is enabled. Current setting: {eeo.allow_autofill ? "Enabled" : "Disabled"}.
      </p>
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

  const setSponsorship = (value: boolean | null) => {
    updateAuth({
      requires_sponsorship: value,
      internship_requires_sponsorship: value,
      full_time_requires_sponsorship: value,
    });
  };

  return (
    <div className="space-y-5">
      <label className="flex items-start gap-3 rounded-xl border border-primary/30 bg-primary/5 px-4 py-3 text-sm">
        <input
          type="checkbox"
          className="mt-1"
          checked={eeo.allow_autofill}
          onChange={(e) => updateEeo({ allow_autofill: e.target.checked })}
        />
        <span>
          <strong>Allow voluntary EEO autofill on job applications.</strong> Required for Gender,
          Disability, Veteran, Hispanic/Latino, and similar fields to fill automatically.
        </span>
      </label>

      <div className="grid gap-4 md:grid-cols-2">
        <YesNoSelect
          label="Are you authorized to work in the US?"
          value={draft.work_authorization.authorized_to_work_in_us}
          onChange={(value) => updateAuth({ authorized_to_work_in_us: value })}
        />
        <YesNoSelect
          label="Will you now or in the future require sponsorship for employment visa status?"
          value={draft.work_authorization.requires_sponsorship}
          onChange={setSponsorship}
        />
      </div>

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
