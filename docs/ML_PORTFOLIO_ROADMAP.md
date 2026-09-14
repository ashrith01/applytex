# Making ApplyTeX a strong ML engineering project

The strongest project story is a reliable, evaluated system that converts
document evidence into correct actions. A copied extension UI alone provides
little evidence of ML engineering skill. Keep the LaTeX source-preservation and
one-page constraints as a distinctive part of the product.

## Recommended project features

| Feature | Engineering evidence | What to demonstrate |
|---|---|---|
| Grounded answer retrieval | Retrieval, evidence ranking, structured generation | Every draft claim links to a user-approved resume/project fact; unsupported claims abstain |
| Question intent classifier | Dataset design, multilabel classification, error analysis | Current vs future sponsorship, relocation, date/number units, sensitive questions and unknown intent |
| Option selection with abstention | Selective prediction, calibration, constrained outputs | Exact/alias baseline vs semantic model, precision/coverage curve, ambiguity examples |
| Versioned evaluation dataset | Reproducibility and data quality | Annotated synthetic/consented forms; employer/template/time holdouts; dataset cards and labeling guidelines |
| Model comparison and serving | ML systems, routing, caching, deployment | Deterministic baseline, retrieval-only, LLM-only and hybrid ablations; latency/cost/error tradeoffs |
| Evidence tracker and interview view | Data engineering and auditability | Exact application artifact/answers survive profile updates; schema migrations and access controls |
| Failure observability | Production engineering | Redacted traces from scan to resolution to DOM commit; failure taxonomy and replayable fixtures |
| Ranking experiment | Data science and information retrieval | Human-labeled job relevance, ranking metrics and baseline comparison; do not optimize an unvalidated ATS score |

## Best implementation order

1. Finish the correctness/reliability work and publish the dated synthetic versus
   live coverage matrix in [the autofill plan](AUTOFILL_STRATEGY_AND_EVALUATION.md).
2. Add a fact/evidence store and immutable per-application bundles. This provides
   usable provenance for model answers and the requested interview preparation.
3. Build a labeled question-intent and option-selection dataset. Have ambiguous
   cases explicitly labeled `needs_review`; document disagreements and exclusions.
4. Compare a deterministic baseline, embeddings plus nearest-neighbor retrieval,
   and a constrained semantic model. Use held-out employers/templates and report
   errors, unsupported-answer rate, precision/coverage, latency and cost.
5. Add model/prompt/dataset version tracking and a dashboard that reproduces one
   evaluation run from its configuration. Keep raw personal application data out
   of public artifacts.
6. Add the cover-letter/resume library and deploy only after profile isolation,
   artifact access, secrets handling and retention controls are verified.

For an ML engineer role, emphasize serving, evaluation, reliability and monitoring.
For a data scientist role, emphasize dataset design, experiments, uncertainty,
slice analysis and defensible conclusions. For AI engineering, emphasize grounded
generation, tool execution, evidence checks and recovery from failed actions.

## Useful demonstrations and experiments

- Show “Java” selecting Java even when JavaScript comes first, and abstaining if
  only JavaScript exists. Reorder the options and retain the same answer.
- Show current sponsorship = No and future sponsorship = Yes, then remove one
  fact and explain why the combined question becomes unresolved.
- Show a Workday-style later step loading a dropdown only after it is opened;
  count success only after the chosen value remains visible.
- Show an unknown numerical question staying blank and an edited answer being
  preserved on a second fill. Deliberately request replacement and show its scope.
- Compare model variants on unknown/negated/compound questions, using the same
  frozen dataset and budget. Report a failed model example as well as successes.
- Freeze an application, change the general profile, and demonstrate that the
  interview view still shows the exact previously supplied resume and answers.

## Resume wording

Safe wording for the work currently present, after review and verification:

> Built ApplyTeX, a LaTeX-native resume tailoring and application-assistance
> system using Python, FastAPI, SQLite and a Chrome extension, with reviewable
> autofill plans and synthetic browser regression tests.

> Implemented conservative option matching, missing-answer abstention and
> application-form validation checks, covering sponsorship, document handling,
> multi-step flows and preservation of user edits.

After the proposed ML work is implemented, replace descriptions with measured
results: “Compared [models] on [N held-out questions], achieving [precision] at
[coverage], with [p95 latency/cost].” Fill those numbers only from saved reports.
Do not claim universal ATS support, production accuracy, interview-rate lift,
Jobright equivalence, or a trained model that the repository does not yet contain.

Recommended portfolio deliverables: a short demo, architecture diagram, dataset
card, reproducible evaluation command, error-analysis report, model/ablation table,
and a concise README explaining which workflows are synthetic-tested or live-verified.
