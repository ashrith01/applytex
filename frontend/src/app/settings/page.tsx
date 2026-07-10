import { Card, CardDescription, CardTitle } from "@/components/ui/card";

export default function SettingsPage() {
  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-3xl font-extrabold">Settings</h1>
        <p className="mt-2 text-ink-muted">LLM routing and advanced options are configured via the backend `.env` file.</p>
      </div>

      <Card>
        <CardTitle>API base URL</CardTitle>
        <CardDescription className="mt-2">
          {process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000"} (set `NEXT_PUBLIC_API_BASE` to override)
        </CardDescription>
      </Card>

      <Card>
        <CardTitle>LLM backends</CardTitle>
        <CardDescription className="mt-2">
          Configure `LLM_BACKEND`, Groq, Anthropic, Ollama, or Codex in `.env`. See docs/MODEL_ROUTING.md.
        </CardDescription>
      </Card>

      <Card>
        <CardTitle>Future hosted mode</CardTitle>
        <CardDescription className="mt-2">
          Authentication and encrypted profile storage will plug in here without changing the main navigation flows.
        </CardDescription>
      </Card>
    </div>
  );
}
