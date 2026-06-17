# Public Harness Export Manifest

This repository is a clean public handoff slice of AutoBCI.

## Included

- Headless CLI package code under `src/bci_autoresearch/`
- Dashboard frontend and local server
- Generic BCI research-loop control-plane docs
- Headless CLI smoke tests
- Provider runtime adapter
- Mobile gateway setup docs for Hermes / WeChat / ClawBot style transports
- Storage-budget guard and disk-growth policy
- Agent-facing harness skill
- Install scripts and license

## Excluded

- `memory/`
- internal strategy notes
- financing drafts
- local recordings or transcripts
- raw data
- generated artifacts
- Kaggle scratch trees and profiler traces
- model checkpoints, ONNX exports, NumPy arrays, and tensorboard event files
- historical BCI training scripts
- historical AutoResearch track manifests
- personal outreach or customer-analysis notes

## Verification Commands

```bash
git rev-list --count HEAD
# Run your local denylist scan before sharing. Keep the denylist outside Git.
PYTHONPATH=src python -m bci_autoresearch.product_shell.cli doctor --json
PYTHONPATH=src python -m bci_autoresearch.product_shell.cli status --json
PYTHONPATH=src python -m bci_autoresearch.product_shell.cli ask "现在进展如何？" --json
PYTHONPATH=src python -m bci_autoresearch.product_shell.cli demo onsite --skip-smoke --json
PYTHONPATH=src pytest -q tests/test_headless_cli.py
```
