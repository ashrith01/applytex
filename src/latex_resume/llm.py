"""LLM backend for the LaTeX resume optimizer.

Supports three wired backends, selected via the ``LLM_BACKEND`` env var
(or the ``LLM_BACKEND`` key in ``.env``):

``groq`` (default when GROQ_API_KEY is set)
    Uses ``groq.AsyncGroq`` with the OpenAI-compatible API.  Fast inference
    for hosted open-source models.  Requires ``GROQ_API_KEY``.
    Env vars: GROQ_API_KEY, GROQ_MODEL (default: qwen/qwen3-32b)

``anthropic``
    Uses ``anthropic.AsyncAnthropic``.  Requires ``ANTHROPIC_API_KEY``.
    Env vars: ANTHROPIC_API_KEY, ANTHROPIC_MODEL (default: claude-sonnet-4-6)

``ollama``
    Uses Ollama's native REST API over ``httpx``.  No API key required.
    Env vars: OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_MODEL_JD,
    OLLAMA_MODEL_PLAN, OLLAMA_MODEL_DIFF, OLLAMA_MODEL_REFINE,
    OLLAMA_MODEL_REVIEW, OLLAMA_NUM_CTX

``codex``
    Uses the official ``openai-codex`` Python SDK to control a local Codex
    app-server. This can use the same Codex authentication path as the Codex
    CLI/IDE/Web. Env vars: CODEX_MODEL, CODEX_SANDBOX.

OpenAI and Gemini env placeholders may exist for future use, but the direct
OpenAI API and Gemini backends are not implemented in ``complete_json`` yet.

Shared contract
---------------
- ``complete_json(prompt, *, system, retries, task)`` — single public entry point.
- ``_extract_json(text)`` — 3-strategy JSON extraction (direct / fenced / brace).
- ``_sanitize_user_input(text)`` — prompt-injection scrubber.
"""

from __future__ import annotations

import json
import logging
import os
import re
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import httpx

from latex_resume.prompts import _INJECTION_PATTERNS

logger = logging.getLogger(__name__)

_USAGE: ContextVar[dict[str, int] | None] = ContextVar(
    "smartjobapply_llm_usage",
    default=None,
)


def reset_usage() -> None:
    """Reset token counters for the current async execution context."""
    _USAGE.set({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})


def get_usage() -> dict[str, int]:
    """Return token counters collected in the current execution context."""
    return dict(_USAGE.get() or {})


def _record_usage(prompt_tokens: Any, completion_tokens: Any) -> None:
    """Accumulate provider token usage when numeric counts are available."""
    try:
        prompt = int(prompt_tokens)
        completion = int(completion_tokens)
    except (TypeError, ValueError):
        return
    usage = dict(_USAGE.get() or {})
    usage["prompt_tokens"] = usage.get("prompt_tokens", 0) + prompt
    usage["completion_tokens"] = usage.get("completion_tokens", 0) + completion
    usage["total_tokens"] = usage.get("total_tokens", 0) + prompt + completion
    _USAGE.set(usage)

# ---------------------------------------------------------------------------
# Load .env (project root) before reading any env vars
# ---------------------------------------------------------------------------

try:
    from dotenv import load_dotenv as _load_dotenv
    # Walk up from this file's directory to find the project root .env
    _here = Path(__file__).resolve()
    for _parent in [_here.parent, _here.parent.parent, _here.parent.parent.parent]:
        _env_file = _parent / ".env"
        if _env_file.exists():
            _load_dotenv(_env_file, override=False)  # env vars already set take precedence
            logger.debug("Loaded .env from %s", _env_file)
            break
except ImportError:
    pass  # python-dotenv not installed; rely on shell-level env vars

# ---------------------------------------------------------------------------
# Configuration (read once at import; tests may patch os.environ before import)
# ---------------------------------------------------------------------------

# Which backend to use: "groq" | "anthropic" | "ollama" | "codex"
LLM_BACKEND: str = os.environ.get("LLM_BACKEND", "ollama").lower()

# Optional per-task routing. Falls back to LLM_BACKEND when unset.
LLM_BACKEND_JD: str = os.environ.get("LLM_BACKEND_JD", LLM_BACKEND).lower()
LLM_BACKEND_PLAN: str = os.environ.get("LLM_BACKEND_PLAN", LLM_BACKEND).lower()
LLM_BACKEND_DIFF: str = os.environ.get("LLM_BACKEND_DIFF", LLM_BACKEND).lower()
LLM_BACKEND_REFINE: str = os.environ.get("LLM_BACKEND_REFINE", LLM_BACKEND).lower()
LLM_BACKEND_REVIEW: str = os.environ.get("LLM_BACKEND_REVIEW", LLM_BACKEND).lower()

# Groq settings
GROQ_MODEL: str = os.environ.get("GROQ_MODEL", "qwen/qwen3-32b")

# Anthropic settings
ANTHROPIC_MODEL: str = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

# OpenAI settings
OPENAI_MODEL: str = os.environ.get("OPENAI_MODEL", "gpt-4o")

# Codex SDK settings
CODEX_MODEL: str = os.environ.get("CODEX_MODEL", "gpt-5.5")
CODEX_SANDBOX: str = os.environ.get("CODEX_SANDBOX", "read_only")

# Gemini settings
GEMINI_MODEL: str = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")

# Ollama settings
OLLAMA_BASE_URL: str = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.environ.get("OLLAMA_MODEL", "qwen3:4b")
OLLAMA_MODEL_JD: str = os.environ.get("OLLAMA_MODEL_JD", OLLAMA_MODEL)
OLLAMA_MODEL_PLAN: str = os.environ.get("OLLAMA_MODEL_PLAN", "qwen3:8b")
OLLAMA_MODEL_DIFF: str = os.environ.get("OLLAMA_MODEL_DIFF", "qwen2.5-coder:7b")
OLLAMA_MODEL_REFINE: str = os.environ.get("OLLAMA_MODEL_REFINE", OLLAMA_MODEL)
OLLAMA_MODEL_REVIEW: str = os.environ.get("OLLAMA_MODEL_REVIEW", OLLAMA_MODEL)
# Default Ollama context is only 2048, which is far too small for resume + JD
# prompts. 16384 fits the current compact optimizer prompts comfortably.
OLLAMA_NUM_CTX: int = int(os.environ.get("OLLAMA_NUM_CTX", "16384"))

MAX_TOKENS: int = 4096
LLM_TEMPERATURE: float = float(os.environ.get("LLM_TEMPERATURE", "0.3"))
OLLAMA_MAX_TOKENS_JD: int = int(os.environ.get("OLLAMA_MAX_TOKENS_JD", "1200"))
OLLAMA_MAX_TOKENS_PLAN: int = int(os.environ.get("OLLAMA_MAX_TOKENS_PLAN", "900"))
OLLAMA_MAX_TOKENS_DIFF: int = int(os.environ.get("OLLAMA_MAX_TOKENS_DIFF", "2200"))
OLLAMA_MAX_TOKENS_REFINE: int = int(os.environ.get("OLLAMA_MAX_TOKENS_REFINE", "700"))
OLLAMA_MAX_TOKENS_REVIEW: int = int(os.environ.get("OLLAMA_MAX_TOKENS_REVIEW", "900"))
# Groq free tier caps total requested tokens (prompt + max_completion) at 6000 TPM.
# Keeping Groq completions at 1500 leaves room for prompts up to ~4500 tokens.
# Upgrade to Groq Dev tier for higher limits (Stage 4 diff prompt is ~5500 tokens).
GROQ_MAX_TOKENS: int = int(os.environ.get("GROQ_MAX_TOKENS", "1500"))
DEFAULT_RETRIES: int = 2  # extra retries on JSON decode failure

# Pre-compiled injection patterns
_INJECTION_RE: list[re.Pattern[str]] = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]

# Regex to strip ```json … ``` fences that sometimes leak into LLM output
_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.DOTALL)

# Lazy-initialised Anthropic client
_anthropic_client: Any = None


def backend_for_task(
    task: str | None = None,
    override_backend: str | None = None,
) -> str:
    """Return the configured backend for an optimizer task."""
    if override_backend:
        return override_backend.lower()
    if not task:
        return LLM_BACKEND
    return {
        "jd": LLM_BACKEND_JD,
        "plan": LLM_BACKEND_PLAN,
        "diff": LLM_BACKEND_DIFF,
        "refine": LLM_BACKEND_REFINE,
        "review": LLM_BACKEND_REVIEW,
    }.get(task, LLM_BACKEND)


def ollama_model_for_task(
    task: str | None = None,
    override_model: str | None = None,
) -> str:
    """Return the configured Ollama model for an optimizer task."""
    if override_model:
        return override_model
    if not task:
        return OLLAMA_MODEL
    return {
        "jd": OLLAMA_MODEL_JD,
        "plan": OLLAMA_MODEL_PLAN,
        "diff": OLLAMA_MODEL_DIFF,
        "refine": OLLAMA_MODEL_REFINE,
        "review": OLLAMA_MODEL_REVIEW,
    }.get(task, OLLAMA_MODEL)


def ollama_max_tokens_for_task(task: str | None = None) -> int:
    """Return the Ollama generation cap for an optimizer task."""
    if not task:
        return MAX_TOKENS
    return {
        "jd": OLLAMA_MAX_TOKENS_JD,
        "plan": OLLAMA_MAX_TOKENS_PLAN,
        "diff": OLLAMA_MAX_TOKENS_DIFF,
        "refine": OLLAMA_MAX_TOKENS_REFINE,
        "review": OLLAMA_MAX_TOKENS_REVIEW,
    }.get(task, MAX_TOKENS)


def codex_model_for_task(
    task: str | None = None,
    override_model: str | None = None,
) -> str:
    """Return the configured Codex model for an optimizer task."""
    return override_model or CODEX_MODEL


def model_for_backend_task(
    backend: str,
    task: str | None = None,
    override_model: str | None = None,
) -> str:
    """Return the effective model label for a backend/task pair."""
    if override_model:
        return override_model

    normalized = backend.lower()
    if normalized == "ollama":
        return ollama_model_for_task(task)
    if normalized == "codex":
        return codex_model_for_task(task)
    if normalized == "groq":
        return GROQ_MODEL
    if normalized == "anthropic":
        return ANTHROPIC_MODEL
    if normalized == "openai":
        return OPENAI_MODEL
    if normalized == "gemini":
        return GEMINI_MODEL
    return "?"


# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------


def _sanitize_user_input(text: str) -> str:
    """Strip known prompt-injection patterns, replacing each with ``[REMOVED]``."""
    for pattern in _INJECTION_RE:
        if pattern.search(text):
            logger.warning("Prompt-injection pattern removed: %s", pattern.pattern)
            text = pattern.sub("[REMOVED]", text)
    return text


# ---------------------------------------------------------------------------
# JSON extraction (shared by both backends)
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> Any:
    """Extract the first valid JSON object from *text*.

    Tries (in order):
    1. Direct ``json.loads`` — model obeyed the JSON-only instruction.
    2. Content inside the first ``` ```json … ``` ``` fence.
    3. Substring from the first ``{`` to the last ``}`` (last-resort fallback).

    Raises ``json.JSONDecodeError`` if none of the three strategies succeeds.
    """
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fence_match = _FENCE_RE.search(text)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("No valid JSON object found in LLM response", text, 0)


# ---------------------------------------------------------------------------
# Anthropic backend
# ---------------------------------------------------------------------------


def _get_anthropic_client() -> Any:
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic as _anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY is not set. "
                "Set LLM_BACKEND=ollama to use Ollama instead."
            )
        _anthropic_client = _anthropic.AsyncAnthropic(api_key=api_key)
    return _anthropic_client


async def _complete_anthropic(prompt: str, system: str, retries: int) -> Any:
    import anthropic as _anthropic

    client = _get_anthropic_client()
    last_error: Exception | None = None

    for attempt in range(1 + retries):
        try:
            response = await client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            raw: str = response.content[0].text
            _record_usage(
                getattr(response.usage, "input_tokens", None),
                getattr(response.usage, "output_tokens", None),
            )
            return _extract_json(raw)
        except json.JSONDecodeError as exc:
            last_error = exc
            logger.warning("Anthropic: JSON decode failed (attempt %d/%d)", attempt + 1, 1 + retries)
        except _anthropic.APIError:
            raise

    raise ValueError(
        f"Anthropic did not return valid JSON after {1 + retries} attempt(s). "
        f"Last error: {last_error}"
    ) from last_error


# ---------------------------------------------------------------------------
# Ollama backend  (OpenAI-compatible /v1/chat/completions)
# ---------------------------------------------------------------------------


async def _complete_ollama(
    prompt: str,
    system: str,
    retries: int,
    task: str | None = None,
    model_override: str | None = None,
) -> Any:
    """Call Ollama via the native /api/chat endpoint.

    We intentionally use the native Ollama API (not the OpenAI-compatible
    /v1/chat/completions shim) because the shim caches the model at the
    context size of the first call and ignores ``num_ctx`` on subsequent
    calls.  The native endpoint applies ``num_ctx`` per-request, forcing
    an in-place context extension when the prompt is large.
    """
    url = f"{OLLAMA_BASE_URL.rstrip('/')}/api/chat"
    headers = {"Content-Type": "application/json"}
    model = ollama_model_for_task(task, model_override)
    num_predict = ollama_max_tokens_for_task(task)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "think": False,
        # Ask Ollama for structured JSON output
        "format": "json",
        "options": {
            "temperature": LLM_TEMPERATURE,
            "num_predict": num_predict,
            "num_ctx": OLLAMA_NUM_CTX,   # applied per-request on native endpoint
        },
    }

    last_error: Exception | None = None

    async with httpx.AsyncClient(timeout=300.0) as client:
        for attempt in range(1 + retries):
            try:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()

                # Native /api/chat response shape:
                # {"message": {"role": "assistant", "content": "..."}, "done": true,
                #  "prompt_eval_count": N, "eval_count": N, ...}
                raw: str = data["message"]["content"]
                prompt_tokens = data.get("prompt_eval_count", "?")
                output_tokens = data.get("eval_count", "?")
                _record_usage(prompt_tokens, output_tokens)
                done_reason = data.get("done_reason", "stop")

                logger.info(
                    "Ollama response: done_reason=%s prompt_tokens=%s out_tokens=%s content_len=%d",
                    done_reason, prompt_tokens, output_tokens, len(raw),
                )
                if done_reason == "length":
                    logger.warning(
                        "Ollama hit num_predict limit (%d) — response may be truncated",
                        num_predict,
                    )
                if not raw.strip():
                    logger.warning(
                        "Ollama returned empty content (done_reason=%s, prompt_tokens=%s). "
                        "Prompt may exceed num_ctx=%d.",
                        done_reason, prompt_tokens, OLLAMA_NUM_CTX,
                    )
                    raise json.JSONDecodeError("Empty response from Ollama", "", 0)
                logger.debug("Ollama raw: %s…", raw[:300])
                return _extract_json(raw)
            except json.JSONDecodeError as exc:
                last_error = exc
                logger.warning(
                    "Ollama: JSON decode failed (attempt %d/%d) — %s",
                    attempt + 1,
                    1 + retries,
                    exc,
                )
            except httpx.HTTPStatusError as exc:
                logger.error("Ollama HTTP error: %s", exc)
                raise
            except httpx.RequestError as exc:
                logger.error("Ollama connection error: %s — is Ollama running?", exc)
                raise

    raise ValueError(
        f"Ollama ({model}) did not return valid JSON after {1 + retries} attempt(s). "
        f"Last error: {last_error}"
    ) from last_error


# ---------------------------------------------------------------------------
# Groq backend  (OpenAI-compatible; uses groq package)
# ---------------------------------------------------------------------------

_groq_client: Any = None


def _get_groq_client() -> Any:
    global _groq_client
    if _groq_client is None:
        import groq as _groq

        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "GROQ_API_KEY is not set. "
                "Add it to .env or export it before running."
            )
        _groq_client = _groq.AsyncGroq(api_key=api_key)
    return _groq_client


_GROQ_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


async def _complete_groq(
    prompt: str,
    system: str,
    retries: int,
    model_override: str | None = None,
) -> Any:
    """Call Groq via the official ``groq`` async client.

    Groq is OpenAI-compatible but returns structured output via the same
    ``choices[0].message.content`` shape.  We do NOT use ``response_format``
    JSON mode here because not all Groq-hosted models support it — instead
    the three-strategy ``_extract_json`` fallback handles the response.

    Reasoning models (qwen3-32b, deepseek-r1, etc.) prepend a ``<think>``
    block before their actual answer.  That block is stripped before JSON
    extraction so it never interferes with parsing.
    """
    import groq as _groq

    client = _get_groq_client()
    last_error: Exception | None = None
    model = model_override or GROQ_MODEL
    user_prompt = prompt
    if "qwen" in model.lower() and "/no_think" not in user_prompt:
        # Qwen reasoning models can spend the response budget on thinking and
        # never reach the JSON object. Ask explicitly for non-thinking mode.
        user_prompt = user_prompt.rstrip() + "\n\n/no_think"

    for attempt in range(1 + retries):
        try:
            response = await client.chat.completions.create(
                model=model,
                max_completion_tokens=GROQ_MAX_TOKENS,
                temperature=LLM_TEMPERATURE,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_prompt},
                ],
            )
            raw: str = response.choices[0].message.content or ""
            prompt_tokens = getattr(response.usage, "prompt_tokens", "?")
            output_tokens = getattr(response.usage, "completion_tokens", "?")
            _record_usage(prompt_tokens, output_tokens)
            finish_reason = response.choices[0].finish_reason
            logger.info(
                "Groq response: finish=%s prompt_tokens=%s out_tokens=%s content_len=%d",
                finish_reason, prompt_tokens, output_tokens, len(raw),
            )
            if not raw.strip():
                raise json.JSONDecodeError("Empty response from Groq", "", 0)
            # Strip <think>…</think> blocks emitted by reasoning models
            raw = _GROQ_THINK_RE.sub("", raw).strip()
            logger.debug("Groq raw (post-strip): %s…", raw[:200])
            return _extract_json(raw)
        except json.JSONDecodeError as exc:
            last_error = exc
            logger.warning(
                "Groq: JSON decode failed (attempt %d/%d) — %s",
                attempt + 1, 1 + retries, exc,
            )
        except _groq.APIError:
            raise

    raise ValueError(
        f"Groq ({model}) did not return valid JSON after {1 + retries} attempt(s). "
        f"Last error: {last_error}"
    ) from last_error


# ---------------------------------------------------------------------------
# Codex SDK backend  (local Codex app-server controlled by openai-codex)
# ---------------------------------------------------------------------------


def _codex_sandbox() -> Any:
    """Return the configured Codex Sandbox enum value."""
    from openai_codex import Sandbox

    normalized = CODEX_SANDBOX.lower().replace("-", "_")
    if normalized in {"read_only", "readonly", "read"}:
        return Sandbox.read_only
    if normalized in {"workspace_write", "workspace", "write"}:
        return Sandbox.workspace_write
    if normalized in {"full_access", "full"}:
        return Sandbox.full_access
    raise ValueError(
        f"Unknown CODEX_SANDBOX={CODEX_SANDBOX!r}. "
        "Choose: read_only | workspace_write | full_access"
    )


async def _complete_codex(
    prompt: str,
    system: str,
    retries: int,
    task: str | None = None,
    model_override: str | None = None,
) -> Any:
    """Call the official Codex SDK and parse the final response as JSON.

    Codex is an agent interface rather than a plain completion API. For this app
    we use it conservatively as a read-only JSON generation backend: each call
    starts a short-lived thread, asks for JSON only, and validates the final
    response with the same JSON extraction gates as every other backend.
    """
    try:
        from openai_codex import AsyncCodex
    except ImportError as exc:
        raise EnvironmentError(
            "The Codex SDK is not installed. Run `uv sync`, then authenticate "
            "Codex with `codex` or configure an API key if you choose API auth."
        ) from exc

    model = codex_model_for_task(task, model_override)
    sandbox = _codex_sandbox()
    codex_prompt = (
        f"{system}\n\n"
        "You are being used as a JSON backend inside ApplyTeX ATS. "
        "Do not inspect or modify files unless explicitly required by the prompt. "
        "Return only the requested JSON object.\n\n"
        f"{prompt}"
    )
    last_error: Exception | None = None

    for attempt in range(1 + retries):
        try:
            async with AsyncCodex() as codex:
                thread = await codex.thread_start(model=model, sandbox=sandbox)
                result = await thread.run(codex_prompt, sandbox=sandbox)
            raw = getattr(result, "final_response", "") or ""
            usage = getattr(result, "usage", None)
            if usage is not None:
                _record_usage(
                    getattr(usage, "input_tokens", None)
                    or getattr(usage, "prompt_tokens", None),
                    getattr(usage, "output_tokens", None)
                    or getattr(usage, "completion_tokens", None),
                )
            logger.info("Codex SDK response: model=%s content_len=%d", model, len(raw))
            if not raw.strip():
                raise json.JSONDecodeError("Empty response from Codex SDK", "", 0)
            return _extract_json(raw)
        except json.JSONDecodeError as exc:
            last_error = exc
            logger.warning(
                "Codex SDK: JSON decode failed (attempt %d/%d) — %s",
                attempt + 1,
                1 + retries,
                exc,
            )

    raise ValueError(
        f"Codex SDK ({model}) did not return valid JSON after {1 + retries} attempt(s). "
        f"Last error: {last_error}"
    ) from last_error


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def complete_json(
    prompt: str,
    *,
    system: str | None = None,
    retries: int = DEFAULT_RETRIES,
    task: str | None = None,
    backend_override: str | None = None,
    model_override: str | None = None,
) -> Any:
    """Send *prompt* to the configured LLM backend and return parsed JSON.

    Backend is chosen by the task-specific env var when present
    (``LLM_BACKEND_JD``, ``LLM_BACKEND_PLAN``, ``LLM_BACKEND_DIFF``,
    ``LLM_BACKEND_REFINE``, ``LLM_BACKEND_REVIEW``), otherwise by
    ``LLM_BACKEND``. Wired choices are ``groq`` | ``anthropic`` | ``ollama`` |
    ``codex``.

    Override per-call by passing an explicit ``system`` message.

    Raises
    ------
    ValueError
        After all retries are exhausted without valid JSON.
    httpx.RequestError / APIError
        On non-recoverable connection or API errors.
    """
    if system is None:
        system = (
            "You are a precise JSON-only assistant. "
            "Output ONLY a valid JSON object — no prose, no markdown, no code fences."
        )

    backend = backend_for_task(task, backend_override)
    _model_label = model_for_backend_task(backend, task, model_override)
    logger.info("complete_json: task=%s backend=%s model=%s", task or "default", backend, _model_label)

    if backend == "groq":
        return await _complete_groq(prompt, system, retries, model_override)
    elif backend == "ollama":
        return await _complete_ollama(prompt, system, retries, task, model_override)
    elif backend == "anthropic":
        return await _complete_anthropic(prompt, system, retries)
    elif backend == "codex":
        return await _complete_codex(prompt, system, retries, task, model_override)
    else:
        raise ValueError(
            f"Unknown LLM_BACKEND={backend!r}. "
            "Choose: groq | anthropic | ollama | codex"
        )
