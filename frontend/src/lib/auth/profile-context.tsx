"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import {
  clearStoredAccessToken,
  getStoredAccessToken,
  setStoredAccessToken,
} from "@/lib/api/client";
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
  authRequired: boolean;
  signIn: (username: string, password?: string) => Promise<void>;
  signOut: () => void;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [profileId, setProfileId] = useState<string | null>(null);
  const [activeProfile, setActiveProfile] = useState<ActiveProfileResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [authRequired, setAuthRequired] = useState(false);

  const refresh = useCallback(async () => {
    const status = await api.getAuthStatus();
    setAuthRequired(status.auth_required);

    const stored = getStoredProfileId();
    if (!stored) {
      setProfileId(null);
      setActiveProfile(null);
      return;
    }
    if (status.auth_required && !getStoredAccessToken()) {
      clearStoredProfileId();
      setProfileId(null);
      setActiveProfile(null);
      return;
    }
    try {
      await api.setActiveProfile(stored);
      const active = await api.getActiveProfile(stored);
      setProfileId(active.profile_id);
      setActiveProfile(active);
    } catch {
      clearStoredProfileId();
      clearStoredAccessToken();
      setProfileId(null);
      setActiveProfile(null);
    }
  }, []);

  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, [refresh]);

  const signIn = useCallback(async (username: string, password?: string) => {
    const normalized = normalizeUsername(username);
    const status = await api.getAuthStatus(normalized);
    setAuthRequired(status.auth_required);

    if (status.auth_required) {
      if (!password || password.length < 8) {
        throw new Error("Password must be at least 8 characters when auth is enabled.");
      }
      const login = await api.login(normalized, password, !status.has_password);
      setStoredAccessToken(login.access_token);
    } else {
      clearStoredAccessToken();
    }

    setStoredProfileId(normalized);
    await api.setActiveProfile(normalized);
    const active = await api.getActiveProfile(normalized);
    setProfileId(active.profile_id);
    setActiveProfile(active);
  }, []);

  const signOut = useCallback(() => {
    clearStoredProfileId();
    clearStoredAccessToken();
    setProfileId(null);
    setActiveProfile(null);
  }, []);

  const value = useMemo(
    () => ({
      profileId,
      activeProfile,
      loading,
      authRequired,
      signIn,
      signOut,
      refresh,
    }),
    [profileId, activeProfile, loading, authRequired, signIn, signOut, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
