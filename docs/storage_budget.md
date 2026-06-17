# Storage Budget and Large Artifact Policy

AutoBCI should not make a user's machine run out of disk space just because they
try the public harness.

## Default Rule

The public harness does not download Kaggle data, raw BCI data, or third-party
datasets by default. Users must point AutoBCI at a local dataset explicitly:

```bash
autobci data set /absolute/path/to/dataset
```

Runtime writes stay under ignored local directories such as `artifacts/` and
`.autobci/`. These directories are audit evidence, not source code.

## Built-In Budgets

Public runner adapters should check storage before doing expensive work:

| Budget | Default | Environment override |
| --- | ---: | --- |
| Dataset directory | 2 GiB | `AUTOBCI_MAX_DATASET_BYTES` |
| Artifact directory | 512 MiB | `AUTOBCI_MAX_ARTIFACT_BYTES` |

Accepted values include raw bytes and human units:

```bash
AUTOBCI_MAX_DATASET_BYTES=10G
AUTOBCI_MAX_ARTIFACT_BYTES=2G
```

Setting either value to `0` disables that guard. That should be an explicit
operator choice, not a default.

## Why This Exists

An internal NeuroGolf throwaway experiment created a 118 GiB `kaggle/` tree even
though the input data was only about 99 MiB. The growth came from generated JSON
artifacts:

| Path class | Size |
| --- | ---: |
| input `data/` | 99 MiB |
| `runs/autoresearch` | 35 GiB |
| `submissions/*/score_traces` | 83 GiB |

The largest files were ONNX Runtime profiling traces written as Chrome trace
JSON. A single task trace reached hundreds of MiB, and repeated rescoring copied
similar traces into multiple submission folders.

That is useful for a targeted debug session, but it is the wrong default for a
public research-loop harness.

## Operator Guidance

Keep large datasets outside the Git checkout when possible:

```bash
mkdir -p ~/AutoBCI-data
autobci data set ~/AutoBCI-data/my_dataset
```

Inspect local growth with:

```bash
du -sh data artifacts .autobci kaggle 2>/dev/null
```

Run a non-destructive AutoBCI storage audit with:

```bash
autobci storage audit --json
```

This scans `artifacts/`, `output/`, `tmp/`, and `.autobci/` for duplicate large
files and text-heavy records that are good compression candidates. It is
`audit_only`: it does not delete, compress, move, or rewrite files.

If a run needs more space, first decide whether the extra data is source data,
debug trace, or audit evidence. Increase the budget only for the category you
intend to grow.

## Policy For New Runners

New domain adapters should follow the same contract:

1. never download data by default;
2. check dataset size before hashing, training, or feature extraction;
3. check artifact size before appending repeated run outputs;
4. keep profiler traces opt-in and off by default;
5. write compact summaries by default, not full tensor dumps or per-step traces.

## Recording Mechanism Policy

The audit trail should be complete enough to replay decisions, not large enough
to mirror every intermediate tensor.

Use this split by default:

| Record type | Default behavior |
| --- | --- |
| Metrics, decisions, commands, diff refs | Keep as small JSON/JSONL/Markdown audit records. |
| Large model checkpoints | Keep the selected checkpoint and a manifest; do not save `best` and `last` unless resume genuinely needs both. |
| Recomputable arrays or feature matrices | Prefer manifest + recipe. If cached, store outside Git checkout or behind an explicit cache budget. |
| Share packages | Store either the expanded folder or the archive, not both, unless the duplicate is explicitly labeled as a release artifact. |
| Chat/context exports | Store source references and compressed bundles; avoid copying the same raw JSONL into every package. |
| Profiler traces | Off by default. When enabled, write to an explicit debug directory with a small retention limit. |
| Repeated experiment runs | Retain top-k selected artifacts plus recent-n run summaries; archive or delete failed full artifacts after ledger capture. |

Compression should be format-aware:

- JSONL, JSON, CSV, Markdown, and logs compress well with gzip or zstd.
- NumPy arrays should use compressed `.npz` only when load speed is not the
  bottleneck.
- Model checkpoints usually need retention and external cache policy more than
  generic compression.

Content-addressed storage is useful when multiple packages need the same large
file:

```text
artifacts/blob_store/sha256/<digest>
artifacts/packages/<run_id>/manifest.json
```

The package manifest should point to the blob by hash instead of copying the
file. A release/export command may materialize a standalone zip when the user
asks for a shareable artifact.
