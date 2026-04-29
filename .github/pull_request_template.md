<!--
Thanks for contributing! A few notes before you submit:

- Keep the PR focused. Smaller PRs are easier to review and ship.
- If your change is user-visible, mention it in the README or open a follow-up.
- Do not bump `manifest.json` `version` unless the maintainer asks you to.
- Do not commit real customer numbers, tokens, or other personal data.
-->

## Summary

<!-- One to three sentences: what does this PR do? -->

## Why

<!-- The motivation. What problem does this solve, or what use case does it enable? Link any related issue with `Closes #123` if applicable. -->

## Tests

<!--
What did you add or change in the test suite? If your change is non-trivial,
say which tests cover it. Confirm the suite still passes:
- `scripts/lint` (ruff) clean
- `pytest` green
-->

## Verification

<!--
Manual verification steps you ran, or "N/A, covered by tests".
For changes that touch the live integration, this might be: started the
devcontainer, configured the integration, observed expected behavior.
-->

## Checklist

- [ ] `scripts/lint` passes (or `uvx ruff check .` on the host).
- [ ] `pytest` passes.
- [ ] User-visible changes are reflected in `README.md`.
- [ ] No real customer numbers, tokens, or personal data in the diff.
- [ ] `manifest.json` version is unchanged (unless the maintainer requested a bump).
