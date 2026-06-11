# Public Harness Export Manifest

This repository is a clean public handoff slice of AutoBCI.

## Included

- CLI and TUI package code under `src/bci_autoresearch/`
- Dashboard frontend and local server
- RSVP image-only demo Program
- RSVP image-only runner and minimal regression test
- Provider runtime adapter
- Agent-facing harness skill
- Install scripts and license

## Excluded

- `memory/`
- internal strategy notes
- financing drafts
- local recordings or transcripts
- raw data
- generated artifacts
- historical BCI training scripts
- historical AutoResearch track manifests
- personal outreach or customer-analysis notes

## Verification Commands

```bash
git rev-list --count HEAD
# Run your local denylist scan before sharing. Keep the denylist outside Git.
PYTHONPATH=src python -m bci_autoresearch.product_shell.cli doctor --json
PYTHONPATH=src python -m bci_autoresearch.product_shell.cli demo onsite --skip-smoke --json
PYTHONPATH=src pytest -q tests/test_rsvp_ship_image_autoresearch.py
```
