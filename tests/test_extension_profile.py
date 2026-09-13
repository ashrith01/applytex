"""Behavior checks for the standalone extension profile helper module."""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).parent.parent
MODULE = ROOT / "extension" / "panel-profile.js"
NODE = shutil.which("node")


def run_profile_helper(body: str) -> Any:
    """Evaluate a small expression against the browser-free profile helper."""
    script = textwrap.dedent(
        f"""
        const fs = require("node:fs");
        eval(fs.readFileSync({json.dumps(str(MODULE))}, "utf8"));
        const helper = globalThis.ApplyTexPanelProfile;
        const result = (() => {{
          {body}
        }})();
        process.stdout.write(JSON.stringify(result));
        """
    )
    completed = subprocess.run(
        [NODE or "node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


@pytest.mark.skipif(NODE is None, reason="Node.js is required for extension helper tests")
def test_profile_helper_builds_minimal_section_patches() -> None:
    result = run_profile_helper(
        """
        const profile = helper.normalizeProfile({
          profile_id: "test",
          full_name: "Test Candidate",
          email: "test@example.com",
          address: { city: "Houston", country: "United States" },
          resume_filename: "resume.tex",
          resume_pdf_filename: "resume.pdf",
          work_authorization: { authorized_to_work_in_us: true },
          application_facts: {
            is_at_least_18: true,
            willing_to_relocate: false,
            willing_to_travel: true,
            active_non_compete_or_non_solicit: false,
            company_relationships: { Example: { currently_employed: false } },
            compensation_preferences: [{
              application_id: null,
              employment_type: "internship",
              amount: "35",
              currency: "USD",
              period: "hourly",
            }],
          },
          search_preferences: { target_roles: ["ai_intern"] },
          custom_answers: { "Earliest start date": "Immediately" },
        });
        const personal = helper.patchForSection("personal", profile);
        const eligibility = helper.patchForSection("eligibility", profile);
        const preferences = helper.patchForSection("preferences", profile);
        return {
          personalKeys: Object.keys(personal).sort(),
          personalHasResume: Object.keys(personal).some((key) => key.includes("resume")),
          eligibilityKeys: Object.keys(eligibility).sort(),
          eligibilityFactKeys: Object.keys(eligibility.application_facts).sort(),
          preferenceFactKeys: Object.keys(preferences.application_facts).sort(),
        };
        """
    )

    assert result["personalKeys"] == [
        "address",
        "email",
        "first_name",
        "full_name",
        "github_url",
        "last_name",
        "linkedin_url",
        "location",
        "phone",
        "portfolio_url",
    ]
    assert result["personalHasResume"] is False
    assert result["eligibilityKeys"] == ["application_facts", "work_authorization"]
    assert result["eligibilityFactKeys"] == [
        "active_non_compete_or_non_solicit",
        "is_at_least_18",
        "willing_to_relocate",
        "willing_to_travel",
    ]
    assert result["preferenceFactKeys"] == ["compensation_preferences"]


@pytest.mark.skipif(NODE is None, reason="Node.js is required for extension helper tests")
def test_profile_workspace_keeps_company_specific_answers_internal() -> None:
    result = run_profile_helper(
        """
        const profile = helper.normalizeProfile({
          full_name: "Test Candidate",
          application_facts: {
            company_relationships: {
              Daikin: {
                currently_employed: false,
                employed_by_affiliate: false,
                previously_employed: false,
              },
            },
            compensation_preferences: [],
          },
          search_preferences: {},
          custom_answers: {},
        });
        const html = helper.renderWorkspace({
          profile,
          section: "preferences",
          errors: [],
          saving: false,
          saveStatus: "",
        });
        return {
          showsCompany: html.includes("Daikin"),
          showsRelationships: html.includes("Company relationships"),
          retainedInternally: Object.hasOwn(profile.application_facts.company_relationships, "Daikin"),
        };
        """
    )

    assert result == {
        "showsCompany": False,
        "showsRelationships": False,
        "retainedInternally": True,
    }


@pytest.mark.skipif(NODE is None, reason="Node.js is required for extension helper tests")
def test_profile_helper_validates_and_manages_repeated_records() -> None:
    result = run_profile_helper(
        """
        const profile = helper.normalizeProfile({
          email: "not-an-email",
          github_url: "javascript:alert(1)",
          educations: [{ school: "University", start_date: "2025-13", end_date: "2024-01" }],
          work_experiences: [],
          application_facts: { compensation_preferences: [{
            application_id: null,
            employment_type: "full_time",
            amount: "0",
            currency: "US",
            period: "annual",
          }] },
          search_preferences: {},
          custom_answers: {},
        });
        const personalErrors = helper.validateSection("personal", profile);
        const educationErrors = helper.validateSection("education", profile);
        const preferenceErrors = helper.validateSection("preferences", profile);
        helper.addRecord(profile, "work");
        helper.setPath(profile, "work_experiences.0.company", "Example");
        helper.setPath(profile, "work_experiences.0.job_title", "Engineer");
        helper.addRecord(profile, "work");
        helper.moveRecord(profile, "work", 1, -1);
        helper.removeRecord(profile, "work", 0);
        return {
          personalErrors,
          educationErrors,
          preferenceErrors,
          workCount: profile.work_experiences.length,
          remainingCompany: profile.work_experiences[0].company,
        };
        """
    )

    assert "Enter a valid email address." in result["personalErrors"]
    assert any("GitHub" in error for error in result["personalErrors"])
    assert any("start date" in error for error in result["educationErrors"])
    assert any("positive number" in error for error in result["preferenceErrors"])
    assert any("currency code" in error for error in result["preferenceErrors"])
    assert result["workCount"] == 1
    assert result["remainingCompany"] == "Example"


@pytest.mark.skipif(NODE is None, reason="Node.js is required for extension helper tests")
def test_profile_helper_renders_profile_and_resume_workspaces_without_credentials() -> None:
    result = run_profile_helper(
        """
        const profile = helper.normalizeProfile({
          profile_id: "test",
          full_name: "Test Candidate",
          address: {},
          work_authorization: {},
          equal_opportunity: { allow_autofill: false },
          application_facts: {},
          search_preferences: {},
          custom_answers: {},
        });
        const profileHtml = helper.renderWorkspace({ profile, section: "personal" });
        const resumeHtml = helper.renderResumeWorkspace({
          resumeInfo: { has_pdf: true, has_latex_source: true, resume_pdf_filename: "resume.pdf" },
          hasJob: true,
        });
        return {
          hasProfileWorkspace: profileHtml.includes("data-profile-workspace"),
          hasSave: profileHtml.includes("Save changes"),
          hasPassword: profileHtml.toLowerCase().includes("password"),
          hasProfileResume: resumeHtml.includes("Use profile resume"),
          hasTailor: resumeHtml.includes("Tailor for this job"),
        };
        """
    )

    assert result == {
        "hasProfileWorkspace": True,
        "hasSave": True,
        "hasPassword": False,
        "hasProfileResume": True,
        "hasTailor": True,
    }
