"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, usePathname } from "next/navigation";
import { ChevronDown, LogIn, LogOut, UserRound } from "lucide-react";
import { MobileNav, Sidebar } from "@/components/layout/sidebar";
import { useAuth } from "@/lib/auth/profile-context";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export function AppShell({ children }: { children: React.ReactNode }) {
  const { profileId, activeProfile, loading, signOut } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [apiOk, setApiOk] = useState<boolean | null>(null);
  const [accountOpen, setAccountOpen] = useState(false);

  useEffect(() => {
    if (!loading && !profileId && pathname !== "/login") {
      router.replace("/login");
    }
  }, [loading, profileId, pathname, router]);

  useEffect(() => {
    api.health().then(() => setApiOk(true)).catch(() => setApiOk(false));
  }, []);

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-ink-muted">
        Loading...
      </div>
    );
  }

  if (!profileId && pathname !== "/login") {
    return null;
  }

  if (pathname === "/login") {
    return <>{children}</>;
  }

  const accountName =
    activeProfile?.full_name ||
    titleProfileId(activeProfile?.profile_id || profileId || "Profile");

  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <Sidebar className="hidden md:flex" />
      <div className="flex min-h-screen min-w-0 flex-1 flex-col">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-surface-card px-4 py-4 md:px-8">
          <div className="font-mono text-xs uppercase tracking-[0.12em] text-ink-muted">
            Source-preserving tailoring / one-page gate
          </div>
          <div className="relative flex items-center gap-3">
            <Badge tone={apiOk ? "success" : "warning"}>
              API {apiOk ? "connected" : apiOk === false ? "offline" : "checking"}
            </Badge>
            {profileId ? (
              <button
                type="button"
                className="inline-flex items-center gap-2 rounded-md border border-border bg-white px-3 py-2 text-sm font-bold text-ink shadow-sm transition hover:bg-surface-muted"
                onClick={() => setAccountOpen((open) => !open)}
                aria-expanded={accountOpen}
                aria-label="Open account menu"
              >
                <span className="grid h-8 w-8 place-items-center rounded-full bg-surface-source text-primary">
                  <UserRound className="h-4 w-4" />
                </span>
                <span className="max-w-[180px] truncate">{accountName}</span>
                <ChevronDown className="h-4 w-4 text-ink-muted" />
              </button>
            ) : (
              <Link href="/login">
                <Button size="sm">
                  <LogIn className="h-4 w-4" />
                  Log in
                </Button>
              </Link>
            )}
            {accountOpen && profileId && (
              <div className="absolute right-0 top-full z-30 mt-2 w-56 rounded-card border border-border bg-white p-2 shadow-lg">
                <div className="px-3 py-2">
                  <p className="truncate text-sm font-bold text-ink">{accountName}</p>
                  <p className="truncate text-xs text-ink-muted">@{profileId}</p>
                </div>
                <Link
                  href="/profile"
                  className="block rounded-md px-3 py-2 text-sm font-semibold text-ink-muted hover:bg-surface-muted hover:text-ink"
                  onClick={() => setAccountOpen(false)}
                >
                  Profile settings
                </Link>
                <button
                  type="button"
                  className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm font-semibold text-red-700 hover:bg-red-50"
                  onClick={() => {
                    setAccountOpen(false);
                    signOut();
                    router.replace("/login");
                  }}
                >
                  <LogOut className="h-4 w-4" />
                  Log out
                </button>
              </div>
            )}
          </div>
        </header>
        <MobileNav />
        <main className="flex-1 px-4 py-6 md:px-8 md:py-8">{children}</main>
      </div>
    </div>
  );
}

function titleProfileId(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}
