"use client";

import { cn } from "@/lib/utils";

interface SkillConfirmGridProps {
  groups: Record<string, string[]>;
  selected: string[];
  onChange: (skills: string[]) => void;
}

export function SkillConfirmGrid({ groups, selected, onChange }: SkillConfirmGridProps) {
  const toggle = (skill: string) => {
    if (selected.includes(skill)) {
      onChange(selected.filter((s) => s !== skill));
    } else {
      onChange([...selected, skill]);
    }
  };

  return (
    <div className="space-y-6">
      {Object.entries(groups).map(([group, skills]) =>
        skills.length === 0 ? null : (
          <div key={group}>
            <h4 className="mb-3 text-sm font-bold text-ink">{group}</h4>
            <div className="flex flex-wrap gap-2">
              {skills.map((skill) => {
                const active = selected.includes(skill);
                return (
                  <button
                    key={skill}
                    type="button"
                    onClick={() => toggle(skill)}
                    className={cn(
                      "rounded-md border px-4 py-2 text-sm font-semibold transition",
                      active
                        ? "border-primary bg-surface-source text-ink"
                        : "border-border bg-white text-ink-muted hover:border-primary/40",
                    )}
                  >
                    {active ? "Selected: " : ""}{skill}
                  </button>
                );
              })}
            </div>
          </div>
        ),
      )}
    </div>
  );
}
