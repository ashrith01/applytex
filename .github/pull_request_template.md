## Summary

<!-- What changed and why? -->

## Safety And Invariants

- [ ] No-op reconstruction remains byte-identical
- [ ] Locked resume sections remain uneditable
- [ ] No validation or truthfulness gate was weakened
- [ ] Submission-ready output remains one visible page
- [ ] No private resume, JD text, or credentials were added

## Verification

- [ ] `uv run pytest`
- [ ] `uv build`
- [ ] `cd frontend && npm run lint && npm run typecheck && npm run build`
- [ ] Extension flow checked when `extension/` changed
- [ ] Benchmark changelog updated when prompts, scoring, or fixtures changed
