# Model Routing

ApplyTeX ATS should not send every task to one large model. Deterministic code
owns parsing, scoring, validation, state transitions, and form execution.

## Recommended M4 Pro / 16 GB Route

| Route | Model | Use |
|---|---|---|
| Primary reasoning | Codex `gpt-5.5` | Resume planning, difficult rewrites, recruiter review, ambiguous form mapping |
| Fast Codex | Codex `gpt-5.4-mini` | Short classifications, repair prompts, inexpensive retries |
| Local private text | Ollama `qwen3:8b` | JD cleanup, requirement extraction, normalization, offline fallback |
| Local semantic match | Ollama `embeddinggemma` | Job deduplication, resume/JD similarity, related-skill retrieval |

This is the recommended starting set: two generative routes and one small
embedding model.

## Local Alternatives

### Qwen3 14B

The Ollama Q4 build is about 9.3 GB. It should run on a 16 GB Mac, but long
contexts and concurrent applications can create memory pressure. Use it as a
single loaded local generator, not alongside another large model.

### Gemma 3 12B

The Ollama build is about 8.1 GB and supports text and image inputs. It is the
best local candidate for later screenshot or form-layout interpretation. Use
Gemma instead of Qwen when vision matters.

### gpt-oss 20B

The Ollama package is about 14 GB and can technically run with 16 GB memory. It
leaves little room for macOS, context cache, browser, Streamlit, and LaTeX tools,
so it is an evaluation model rather than the default route.

Dense 24B-32B Q4 models generally exceed a comfortable 16 GB working set.
Aggressive quantization or swap can make them launch, but latency and quality
are poor fits for the interactive application.

## Optional Google Route

If a Gemini API key is added later:

- `gemini-3.1-flash-lite` is suitable for high-volume extraction and
  normalization.
- `gemini-3.5-flash` is suitable for stronger multimodal form interpretation.

Google's free tier may use submitted content to improve products. Do not send
private resumes through that tier without informed user consent. A paid tier or
local route is preferable for real candidate data.

## Product Rules

- Model comparison and evidence ledgers stay in tests and developer diagnostics.
- The normal UI presents one result, not provider scoreboards.
- Provider failure cannot bypass truth, one-page, approval, or state checks.
- Browser actions remain deterministic after a reviewed fill plan is produced.
- Cache extraction and embedding outputs by content hash to reduce latency.

