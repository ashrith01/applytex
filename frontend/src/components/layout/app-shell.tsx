"use client";

import { useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import { MobileNav, Sidebar } from "@/components/layout/sidebar";
import { useAuth } from "@/lib/auth/profile-context";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";

export function AppShell({ children }: { children: React.ReactNode }) {
  const { profileId, loading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [apiOk, setApiOk] = useState<boolean | null>(null);

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

  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <Sidebar className="hidden md:flex" />
      <div className="flex min-h-screen min-w-0 flex-1 flex-col">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-surface-card px-4 py-4 md:px-8">
          <div className="font-mono text-xs uppercase tracking-[0.12em] text-ink-muted">
            Source-preserving tailoring / one-page gate
          </div>
          <Badge tone={apiOk ? "success" : "warning"}>
            API {apiOk ? "connected" : apiOk === false ? "offline" : "checking"}
          </Badge>
        </header>
        <MobileNav />
        <main className="flex-1 px-4 py-6 md:px-8 md:py-8">{children}</main>
      </div>
    </div>
  );
}
