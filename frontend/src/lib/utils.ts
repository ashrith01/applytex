import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function scoreLabel(score: number): string {
  if (score >= 80) return "Strong";
  if (score >= 60) return "Fair";
  return "Needs work";
}

export function formatScore(score: number | null | undefined, signed = false): string {
  if (score == null || Number.isNaN(score)) return "—";
  const prefix = signed && score > 0 ? "+" : "";
  return `${prefix}${score.toFixed(1)}`;
}

export function normalizeUsername(username: string): string {
  const normalized = username
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_.-]+/g, "_")
    .replace(/^[._-]+|[._-]+$/g, "");
  return normalized || "default";
}

export function roleLabel(role: string): string {
  return role.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export const PROFILE_STORAGE_KEY = "smartjobapply_profile_id";

export function getStoredProfileId(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(PROFILE_STORAGE_KEY);
}

export function setStoredProfileId(profileId: string): void {
  localStorage.setItem(PROFILE_STORAGE_KEY, profileId);
}

export function clearStoredProfileId(): void {
  localStorage.removeItem(PROFILE_STORAGE_KEY);
}
