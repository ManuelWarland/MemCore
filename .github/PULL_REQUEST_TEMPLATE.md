<!--
Thanks for contributing. MemCore is small on purpose — the bar for new surface
area is high, but bug fixes, docs, host adapters, and test coverage are always
welcome.
-->

## What this changes

<!-- One or two sentences. Link the issue it closes: "Closes #12". -->

## Why

<!-- The problem. If it's a behaviour change, say what was wrong before. -->

## Checklist

- [ ] `python scripts/memcore.py healthcheck` passes
- [ ] The four `scripts/test_*.py` pass (`python scripts/test_phase_a.py` etc.), or CI is green on this branch
- [ ] No secrets, no personal paths, no machine-specific assumptions outside documented adapters
- [ ] Docs updated if behaviour or the public surface changed (`README.md`, `README.fr.md`, `CHANGELOG.md`)
- [ ] Stays a passive store — no automatic capture, no daemon, no mandatory network
