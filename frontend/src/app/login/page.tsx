"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/lib/auth/profile-context";

export default function LoginPage() {
  const { signIn } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await signIn(username);
      router.push("/profile");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not sign in");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-surface px-4">
      <Card className="w-full max-w-md">
        <p className="font-mono text-xs uppercase tracking-[0.14em] text-ink-muted">local profile</p>
        <CardTitle className="mt-2 font-serif text-3xl">Start ApplyTeX ATS</CardTitle>
        <CardDescription className="mt-2">
          Use the same username to load your saved resume source, profile answers, and application review queue.
        </CardDescription>
        <form onSubmit={onSubmit} className="mt-6 space-y-4">
          <label className="block text-sm font-semibold text-ink">
            Username
            <input
              className="mt-2 w-full rounded-md border border-border px-4 py-3 outline-none focus:border-primary"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="yourname"
              required
            />
          </label>
          {error && <p className="text-sm text-red-600">{error}</p>}
          <Button type="submit" className="w-full" disabled={loading}>
            {loading ? "Continuing..." : "Continue"}
          </Button>
        </form>
      </Card>
    </div>
  );
}
