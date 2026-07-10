export const YES_NO_OPTIONS = ["Yes", "No"] as const;

export const YES_NO_DECLINE_OPTIONS = ["Yes", "No", "Decline to state"] as const;

export const GENDER_OPTIONS = ["Male", "Female", "Non-Binary", "Decline to state"] as const;

export const RACE_OPTIONS = [
  "American Indian or Alaskan Native",
  "Asian",
  "Black or African American",
  "Hispanic or Latino",
  "White",
  "Native Hawaiian or Other Pacific Islander",
  "Two or More Races",
] as const;

export const SEXUAL_ORIENTATION_OPTIONS = [
  "Asexual",
  "Bisexual",
  "Gay",
  "Heterosexual",
  "Lesbian",
  "Pansexual",
  "Queer",
  "I prefer to self-describe",
  "Decline to state",
] as const;

export const PRONOUNS_OPTIONS = [
  "He/Him",
  "She/Her",
  "They/Them",
  "Other",
  "Prefer not to say",
] as const;

export type YesNoOption = (typeof YES_NO_OPTIONS)[number];
export type YesNoDeclineOption = (typeof YES_NO_DECLINE_OPTIONS)[number];
export type GenderOption = (typeof GENDER_OPTIONS)[number];
export type RaceOption = (typeof RACE_OPTIONS)[number];
export type SexualOrientationOption = (typeof SEXUAL_ORIENTATION_OPTIONS)[number];
export type PronounsOption = (typeof PRONOUNS_OPTIONS)[number];
