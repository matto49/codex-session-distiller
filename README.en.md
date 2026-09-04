# Codex Session Distiller

[简体中文](README.md) · [MIT License](LICENSE)

**Turn Codex history into source-cited continuation briefs before archiving old tasks through the official interface.**

An installable Codex Skill with standard-library Python scripts. It supports resumable per-task distillation, content-review receipts, conservative retention rules, parent/child protection, durable journals, and original-byte verification after archive or restore.

```text
Read-only snapshot → Distill → Validate and review → Retention plan → Official archive → Readback
```

## Install

Copy `skills/codex-session-distiller` into your Codex skills directory. Inspect an existing installation before replacing it.

```sh
git clone https://github.com/matto49/codex-session-distiller.git
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
cp -R codex-session-distiller/skills/codex-session-distiller \
  "${CODEX_HOME:-$HOME/.codex}/skills/"
```

In a task that can discover the new Skill:

> Use $codex-session-distiller to summarize my Codex history, review the source-cited briefs, and archive eligible old tasks.

A summaries-only request stops before archive. Existing authorization is respected; archiving never implies deletion or permission to publish private run data.

## Run the scripts

Python 3.11+, macOS/Linux, no third-party Python dependencies. Archive/restore additionally requires an official Codex binary exposing the supported app-server protocol. Use the same workflow **on each source host**; automatic SSH provisioning or native database synchronization is outside v0.1.

```sh
python3 skills/codex-session-distiller/scripts/session_distiller.py --help
python3 skills/codex-session-distiller/scripts/session_distiller.py snapshot \
  --out "$HOME/codex-session-runs/first-pass" --label workstation
```

Continue with `distill`, `verify`, `review`, `plan`, `archive`, and `status`. Model adapters are configurable argv arrays. Archive without `--apply` is a preview. See the [complete workflow](skills/codex-session-distiller/references/workflow.md), [model contract](skills/codex-session-distiller/references/summarizer.md), and [compatibility notes](skills/codex-session-distiller/references/compatibility.md).

## What is preserved

- Goals, latest supported outcomes, decisions, evidence, unresolved work, and next steps.
- Original source lines, per-stage summaries, and cached reduction results.
- Original JSONL files, with SHA-256 readback after native archive and restore.
- Recent, pinned, queued, automation-associated, unfinished-goal, incomplete and changed-source tasks; ancestors of protected descendants are kept too.
- Rejections and uncertain outcomes: no blind retry or direct SQLite/file-move fallback.

Structural checks validate identity and citations, not semantic truth. Content review remains required. Images, full tool outputs and opaque compaction are not represented as independently verified evidence. Summaries are read on demand, not automatically injected into future tasks.

## Tests and privacy

```sh
python3 -m unittest discover -s tests -v
```

Tests use temporary synthetic stores and a mock server. They cover archive/restore, source drift, wrong citations, mixed task IDs, parent cascades, active writers, rejected operations and crash reconciliation. The mock refuses directories without a synthetic marker.

The portable release was extracted and rewritten from a real two-host maintenance workflow; synthetic tests do not prove compatibility with every Codex version. Archive reduces active-list clutter; it does not guarantee disk reclamation or a particular latency improvement.

Generated runs contain private transcripts, summaries, paths and diagnostic output. Keep them outside public repositories. Redaction is best effort; your chosen model CLI may contact its provider. The scripts do not upload history themselves, directly edit Codex databases, change authentication, or write generated Codex memories.

MIT licensed for personal and commercial use.
