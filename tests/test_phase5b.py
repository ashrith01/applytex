"""Phase 5b: encryption at rest, per-profile LLM keys and budgets, durable classic sessions."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from latex_resume import llm
from latex_resume.api import create_app
from latex_resume.application_store import ApplicationStore
from latex_resume.data_protection import (
    DataKeyMissing,
    DataProtection,
    generate_key,
    protect_profile_payload,
    unprotect_profile_payload,
)
from latex_resume.job_models import CandidateProfile, JobPosting, JobProvider
from latex_resume.session import SessionStore, deserialize_optimization_result, serialize_optimization_result

SAMPLE_TEX = Path(__file__).parent.parent / "samples" / "sample_resume.tex"


# ---------------------------------------------------------------------------
# encryption at rest
# ---------------------------------------------------------------------------


def test_data_protection_roundtrip_and_missing_key() -> None:
    key = generate_key()
    protection = DataProtection(key)
    sealed = protection.encrypt({"gender": "Non-binary"})
    assert set(sealed) == {"__enc__"} and "Non-binary" not in json.dumps(sealed)
    assert protection.decrypt(sealed) == {"gender": "Non-binary"}
    assert protection.decrypt("plain") == "plain"
    with pytest.raises(DataKeyMissing):
        DataProtection(None).decrypt(sealed)
    with pytest.raises(DataKeyMissing):
        DataProtection(generate_key()).decrypt(sealed)
    off = DataProtection(None)
    assert off.encrypt({"x": 1}) == {"x": 1} and not off.enabled

    payload = {"equal_opportunity": {"gender": "Woman"}, "application_facts": {"compensation_preferences": [{"amount": "150000"}]}, "llm_settings": {"api_key": "sk-secret"}, "full_name": "A"}
    sealed_payload = protect_profile_payload(json.loads(json.dumps(payload)), protection)
    text = json.dumps(sealed_payload)
    assert "Woman" not in text and "150000" not in text and "sk-secret" not in text and '"full_name": "A"' in text
    assert unprotect_profile_payload(sealed_payload, protection) == payload


def test_profile_secrets_are_sealed_in_sqlite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key = generate_key()
    monkeypatch.setenv("APPLYTEX_DATA_KEY", key)
    db = tmp_path / "sealed.db"
    store = ApplicationStore(db)
    assert store.protection.enabled
    profile = store.get_candidate_profile("alice")
    profile.equal_opportunity.gender = "Woman"
    profile.equal_opportunity.allow_autofill = True
    from latex_resume.job_models import CompensationPreference

    profile.application_facts.compensation_preferences = [CompensationPreference(amount="150000", currency="USD", period="annual")]
    profile.llm_settings.api_key = "gsk_super_secret_key"
    profile.full_name = "Alice Example"
    store.save_candidate_profile(profile)

    raw = sqlite3.connect(db).execute("SELECT payload_json FROM candidate_profiles WHERE profile_id = 'alice'").fetchone()[0]
    assert "Woman" not in raw and "150000" not in raw and "gsk_super_secret_key" not in raw and "Alice Example" in raw
    assert "__enc__" in raw

    loaded = ApplicationStore(db).get_candidate_profile("alice")
    assert loaded.equal_opportunity.gender == "Woman"
    assert loaded.application_facts.compensation_preferences[0].amount == "150000"
    assert loaded.llm_settings.api_key == "gsk_super_secret_key"

    # Without the key the row is unreadable rather than silently wrong.
    monkeypatch.delenv("APPLYTEX_DATA_KEY")
    with pytest.raises(DataKeyMissing):
        ApplicationStore(db).get_candidate_profile("alice")
    # Export never includes the LLM key.
    monkeypatch.setenv("APPLYTEX_DATA_KEY", key)
    export = ApplicationStore(db).export_profile_data("alice")
    assert "api_key" not in export["profile"]["llm_settings"]


def test_eeo_receipt_values_are_sealed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPLYTEX_DATA_KEY", generate_key())
    store = ApplicationStore(tmp_path / "receipt-sealed.db")
    job = JobPosting(job_id="j", provider=JobProvider.GREENHOUSE, board_token="a", external_id="1", company="Acme", title="ML", description="x" * 200, source_url="https://e.test/j", apply_url="https://e.test/j/apply")
    store.save_job(job)
    application = store.create_application("j")
    from latex_resume.job_models import SubmissionBundle, SubmissionField

    bundle = SubmissionBundle(
        bundle_id="b1", application_id=application.application_id, job_id="j",
        fields=[SubmissionField(field_id="g", label="Gender", value="Woman", source="eeo_opt_in"), SubmissionField(field_id="n", label="Name", value="Alice", source="profile")],
    )
    store.save_submission_bundle(bundle)
    raw = sqlite3.connect(store.path).execute("SELECT payload_json FROM submission_bundles").fetchone()[0]
    assert "Woman" not in raw and "Alice" in raw
    loaded = store.get_submission_bundle(application.application_id)
    assert [field.value for field in loaded.fields] == ["Woman", "Alice"]


# ---------------------------------------------------------------------------
# per-profile LLM keys and budgets
# ---------------------------------------------------------------------------


def test_budget_check_and_recording_through_complete_json(monkeypatch: pytest.MonkeyPatch) -> None:
    usage = {"calls": 0, "tokens": 0}

    def reader(profile_id: str) -> dict[str, int]:
        return dict(usage)

    def sink(profile_id: str, calls: int, tokens: int) -> dict[str, int]:
        usage["calls"] += calls
        usage["tokens"] += tokens
        return dict(usage)

    seen: list[tuple[str, str | None]] = []

    async def fake_dispatch(backend: str, prompt: str, system: str, retries: int, task: str | None, model_override: str | None, web_search: bool) -> dict[str, Any]:
        seen.append((backend, model_override))
        llm._record_usage(10, 5)
        return {"ok": True}

    monkeypatch.setattr(llm, "_dispatch_backend", fake_dispatch)
    context = llm.ProfileLLMContext(profile_id="alice", backend="groq", api_key="gsk_x", model="llama-x", daily_call_budget=2, usage_reader=reader, usage_sink=sink)
    token = llm.set_profile_llm_context(context)
    try:
        llm.reset_usage()
        assert asyncio.run(llm.complete_json("hi", task="jd")) == {"ok": True}
        assert seen[-1] == ("groq", "llama-x"), "profile routing wins over env defaults"
        assert usage == {"calls": 1, "tokens": 15}
        assert llm.profile_api_key("groq") == "gsk_x" and llm.profile_api_key("openai") is None
        # An explicit per-call override still wins over the profile backend.
        asyncio.run(llm.complete_json("hi", backend_override="ollama"))
        assert seen[-1][0] == "ollama" and usage["calls"] == 2
        with pytest.raises(llm.LLMBudgetExceeded):
            asyncio.run(llm.complete_json("hi"))
        assert usage["calls"] == 2, "a refused call is not counted"
    finally:
        llm.reset_profile_llm_context(token)
    assert llm.current_profile_llm_context() is None


def test_llm_settings_routes_mask_keys_and_enforce_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPLYTEX_DATA_KEY", generate_key())
    store = ApplicationStore(tmp_path / "llm.db")
    profile = store.get_candidate_profile("alice")
    profile.full_name = "Alice"
    store.save_candidate_profile(profile)
    calls: list[str] = []

    async def fake_dispatch(backend: str, prompt: str, system: str, retries: int, task: str | None, model_override: str | None, web_search: bool) -> dict[str, Any]:
        calls.append(backend)
        return {"answers": []}

    monkeypatch.setattr(llm, "_dispatch_backend", fake_dispatch)
    headers = {"X-Profile-Id": "alice"}
    with TestClient(create_app(application_store=store)) as client:
        view = client.get("/profile/llm", headers=headers).json()
        assert view == {"backend": None, "model": None, "has_api_key": False, "api_key_tail": "", "daily_call_budget": None, "daily_token_budget": None, "encrypted_at_rest": True}
        updated = client.put("/profile/llm", json={"backend": "anthropic", "api_key": "sk-ant-1234567890", "daily_call_budget": 1}, headers=headers)
        assert updated.status_code == 200, updated.text
        assert updated.json()["has_api_key"] and updated.json()["api_key_tail"] == "7890"
        assert "sk-ant" not in updated.text
        assert client.get("/profile", headers=headers).json()["llm_settings"]["api_key"] == "sk-ant-1234567890", "the owner can still read their own profile"
        assert "sk-ant" not in json.dumps(client.get("/profile/export", headers=headers).json())

        # A route that calls the model: the profile's backend is used and the budget bites.
        scan = client.post(
            "/extension/forms/scan",
            json={"provider": "greenhouse", "page_url": "https://boards.greenhouse.io/x/jobs/1", "questions": [{"field_id": "q", "label": "How did you hear about us?", "input_type": "select", "options": ["LinkedIn"], "required": True}]},
            headers=headers,
        ).json()
        first = client.post(f"/extension/forms/{scan['scan_id']}/answers/propose", json={}, headers=headers)
        assert first.status_code == 200, first.text
        assert calls == ["anthropic"]
        usage = client.get("/profile/llm/usage", headers=headers).json()
        assert usage["calls"] == 1 and usage["daily_call_budget"] == 1
        second = client.post(f"/extension/forms/{scan['scan_id']}/answers/propose", json={}, headers=headers)
        assert second.status_code == 429 and "budget" in second.json()["detail"]
        assert calls == ["anthropic"], "the model was not called once the budget was exhausted"

        # Another profile is unaffected and the key can be cleared.
        assert client.get("/profile/llm/usage", headers={"X-Profile-Id": "bob"}).json()["calls"] == 0
        cleared = client.put("/profile/llm", json={"api_key": "", "clear_budgets": True}, headers=headers).json()
        assert cleared["has_api_key"] is False and cleared["daily_call_budget"] is None


# ---------------------------------------------------------------------------
# durable classic sessions
# ---------------------------------------------------------------------------


def test_optimization_result_survives_json_roundtrip() -> None:
    from latex_resume.ats import ATSResult
    from latex_resume.optimizer import OptimizationResult

    opt = OptimizationResult(modified_latex="x", pdf_bytes=b"%PDF-1.4 fake", ats_after=ATSResult(score=71.5, required_missing=["Rust"]), diff=[{"stmt_id": "work_0_0"}], warnings=["w"])
    data = serialize_optimization_result(opt)
    assert json.dumps(data, default=str)
    back = deserialize_optimization_result(json.loads(json.dumps(data, default=str)))
    assert back is not None and back.pdf_bytes == b"%PDF-1.4 fake" and back.ats_after.score == 71.5
    assert back.ats_after.required_missing == ["Rust"] and back.diff == opt.diff and back.warnings == ["w"]
    assert deserialize_optimization_result(None) is None


def test_classic_sessions_persist_across_app_restarts(tmp_path: Path) -> None:
    db = tmp_path / "sessions.db"
    with TestClient(create_app(application_store=ApplicationStore(db))) as client:
        upload = client.post("/latex/upload", files={"file": ("r.tex", SAMPLE_TEX.read_bytes(), "text/plain")}, headers={"X-Profile-Id": "alice"})
        assert upload.status_code == 200, upload.text
        session_id = upload.json()["session_id"]
        assert client.get(f"/latex/{session_id}/status", headers={"X-Profile-Id": "alice"}).json()["optimized"] is False
    # Simulate a restart: a fresh in-memory registry bound to the same DB.
    from latex_resume import session as session_module

    session_module.store = SessionStore()
    try:
        with TestClient(create_app(application_store=ApplicationStore(db))) as client:
            status = client.get(f"/latex/{session_id}/status", headers={"X-Profile-Id": "alice"})
            assert status.status_code == 200 and status.json()["filename"] == "r.tex"
            assert client.get(f"/latex/{session_id}/status", headers={"X-Profile-Id": "bob"}).status_code == 404
            rerender = client.post(f"/latex/{session_id}/rerender", json={"changes": {}}, headers={"X-Profile-Id": "alice"})
            assert rerender.status_code == 200 and rerender.json()["applied"] == []
            assert client.delete(f"/latex/{session_id}", headers={"X-Profile-Id": "alice"}).status_code == 204
            assert client.get(f"/latex/{session_id}/status", headers={"X-Profile-Id": "alice"}).status_code == 404
    finally:
        session_module.store = SessionStore()
