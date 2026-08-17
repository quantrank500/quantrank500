# Contributing

Issues and pull requests are welcome. Two things to know:

- **The spec governs design.** Structural rules (commit-reveal, INSERT-only
  ledger, settle-against-the-predictor ambiguity) are not up for casual
  change; open an issue to discuss before building.
- **Tests first.** The engine is pure functions with an adversarial test
  suite — changes to fill/settle behavior need tests that would fail today.

Run `pytest` before submitting. Keep the frontend vanilla: no build step,
no frameworks, no external requests.