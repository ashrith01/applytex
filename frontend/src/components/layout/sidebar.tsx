"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { BriefcaseBusiness, ClipboardCheck, FileText, FlaskConical, LayoutDashboard, Settings, UserRound } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth/profile-context";
import { Button } from "@/components/ui/button";

const nav = [
  { href: "/", label: "Start", icon: LayoutDashboard },
  { href: "/profile", label: "Profile", icon: UserRound },
  { href: "/jobs", label: "Jobs", icon: BriefcaseBusiness },
  { href: "/applications", label: "Applications", icon: ClipboardCheck },
  { href: "/lab", label: "Lab", icon: FlaskConical, muted: true },
  { href: "/settings", label: "Settings", icon: Settings, muted: true },
];

export function Sidebar({ className }: { className?: string }) {
  const pathname = usePathname();
  const { activeProfile, signOut } = useAuth();

  return (
    <aside className={cn("flex min-h-screen w-64 flex-col border-r border-border bg-surface-card px-4 py-6", className)}>
      <div className="mb-8 flex items-center gap-3 px-2">
        <div className="flex h-10 w-10 items-center justify-center rounded-md bg-primary font-mono text-xs font-black text-primary-foreground">
          .tex
        </div>
        <div>
          <div className="font-extrabold text-ink">ApplyTeX ATS</div>
          <div className="font-mono text-xs text-ink-muted">LaTeX MVP</div>
        </div>
      </div>

      <nav className="flex flex-1 flex-col gap-1">
        {nav.map((item) => {
          const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2.5 text-sm font-semibold transition",
                active ? "bg-surface-source text-ink ring-1 ring-primary/20" : "text-ink-muted hover:bg-surface-muted hover:text-ink",
                item.muted && !active && "text-ink-subtle",
              )}
            >
              <Icon className="h-4 w-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="mt-auto space-y-3 border-t border-border pt-4">
        {activeProfile && (
          <div className="px-2">
            <div className="font-bold text-ink">{activeProfile.full_name || activeProfile.profile_id}</div>
            <div className="text-xs text-ink-muted">@{activeProfile.profile_id}</div>
          </div>
        )}
        <Button variant="ghost" size="sm" className="w-full" onClick={signOut}>
          <FileText className="h-4 w-4" />
          Change username
        </Button>
      </div>
    </aside>
  );
}

export function MobileNav() {
  const pathname = usePathname();

  return (
    <nav className="flex gap-2 overflow-x-auto border-b border-border bg-surface-card px-4 py-3 md:hidden">
      {nav.map((item) => {
        const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
        const Icon = item.icon;
        return (
          <Link
            key={item.href}
            href={item.href}
            className={cn(
              "inline-flex shrink-0 items-center gap-2 rounded-md border px-3 py-2 text-sm font-semibold transition",
              active
                ? "border-primary/30 bg-surface-source text-ink"
                : "border-border text-ink-muted hover:bg-surface-muted hover:text-ink",
            )}
          >
            <Icon className="h-4 w-4" />
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
