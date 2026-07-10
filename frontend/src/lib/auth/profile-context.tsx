"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { ActiveProfileResponse } from "@/lib/api/types";
import {
  clearStoredProfileId,
  getStoredProfileId,
  normalizeUsername,
  setStoredProfileId,
} from "@/lib/utils";

interface AuthContextValue {
  profileId: string | null;
  activeProfile: ActiveProfileResponse | null;
  loading: boolean;
  signIn: (username: string) => Promise<void>;
  signOut: () => void;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [profileId, setProfileId] = useState<string | null>(null);
  const [activeProfile, setActiveProfile] = useState<ActiveProfileResponse | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const stored = getStoredProfileId();
    if (!stored) {
      setProfileId(null);
      setActiveProfile(null);
      return;
    }
    try {
      await api.setActiveProfile(stored);
      const active = await api.getActiveProfile();
      setProfileId(active.profile_id);
      setActiveProfile(active);
    } catch {
      clearStoredProfileId();
      setProfileId(null);
      setActiveProfile(null);
    }
  }, []);

  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, [refresh]);

  const signIn = useCallback(async (username: string) => {
    const normalized = normalizeUsername(username);
    setStoredProfileId(normalized);
    await api.setActiveProfile(normalized);
    const active = await api.getActiveProfile();
    setProfileId(active.profile_id);
    setActiveProfile(active);
  }, []);

  const signOut = useCallback(() => {
    clearStoredProfileId();
    setProfileId(null);
    setActiveProfile(null);
  }, []);

  const value = useMemo(
    () => ({ profileId, activeProfile, loading, signIn, signOut, refresh }),
    [profileId, activeProfile, loading, signIn, signOut, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
