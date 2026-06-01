# Documentation

Project docs for the Personal Anki Card Builder. Start with the
[root README](../README.md) for the quickstart.

## Structure

```
docs/
├── guide/      # how the implemented system works
├── status/     # what's done vs. deferred
├── testing/    # test coverage + how to verify
└── specs/      # original requirement & design docs (inputs)
```

## Index

### Guide
- [guide/documentation.md](guide/documentation.md) — architecture, CLI, config,
  DB schema, and the search/filter/rank/card algorithms **as built**.

### Status
- [status/implementation-status.md](status/implementation-status.md) — what's
  implemented vs. deferred, mapped to the original plan (decisions D1–D13, build
  steps 1–8), plus intentional deviations and the next-pass order.

### Testing
- [testing/testing-plan.md](testing/testing-plan.md) — fixtures, the coverage
  matrix (23 tests), manual/live verification, and known gaps.

### Specs (original inputs)
- [specs/implementation_plan_v1.md](specs/implementation_plan_v1.md) —
  architecture + decision log.
- [specs/personal_anki_builder_spec_v2.md](specs/personal_anki_builder_spec_v2.md)
  — v1 product spec.
- [specs/PRD-v1.md](specs/PRD-v1.md) — original PRD.

## Reading order

1. New here? [root README](../README.md) → [guide/documentation.md](guide/documentation.md).
2. Picking up the work? [status/implementation-status.md](status/implementation-status.md).
3. Changing code? [testing/testing-plan.md](testing/testing-plan.md).
