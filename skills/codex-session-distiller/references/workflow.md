# Workflow and commands

All commands below run on the source machine. Python 3.11+ is required. The default Codex home is `$CODEX_HOME` or `~/.codex`; pass `--codex-home` to select another store. Generated run artifacts must stay outside that store and outside a public Git checkout.

From the repository root:

```sh
CSD="skills/codex-session-distiller/scripts/session_distiller.py"
TASK_RUN="$HOME/codex-session-runs/first-pass"

python3 "$CSD" snapshot --out "$TASK_RUN" --label workstation
python3 "$CSD" distill --run "$TASK_RUN" \
  --command-json '["claude","-p","--tools","","--strict-mcp-config","--disable-slash-commands","--output-format","json"]' \
  --limit 3
python3 "$CSD" verify --run "$TASK_RUN"
```

`--limit 3` is a first-run pilot, so `verify` will report missing summaries until the rest are distilled. Remove the limit to finish. Model CLI credentials and model choice come from the user's existing configuration. Do not add `--no-session-persistence`. Cached map/reduction results are bound to task ID and input digest. A new source snapshot requires a new run directory.

Inspect `INDEX.md`, `summaries/SESSION_UUID.md`, and original evidence:

```sh
python3 "$CSD" source --run "$TASK_RUN" --session SESSION_UUID --line 12
python3 "$CSD" review --run "$TASK_RUN" --session SESSION_UUID \
  --note 'Checked final outcome, source citations, unresolved work and next start.'
python3 "$CSD" search --run "$TASK_RUN" 'cache'
```

The note is a receipt for review that actually happened; it is not a substitute for that review. `--session` may be repeated for tasks individually reviewed in the same pass. Editing a summary invalidates its review receipt. Structural `verify` checks source identity and locators, not truth or semantic completeness.

Prepare a plan after reviews:

```sh
python3 "$CSD" plan --run "$TASK_RUN" --inactive-days 30 \
  --protected-ids /path/to/current-protected-ids.json
python3 "$CSD" archive --run "$TASK_RUN"
```

The protected-ID file is an optional JSON array of UUIDs, refreshed from the coordinating app. It is reread during apply. If used, keep it available at that same path. A retained child also retains its parent. Any new task not in the snapshot is protected.

With the user's archive authorization, apply using the **official native binary** discovered on this machine:

```sh
python3 "$CSD" archive --run "$TASK_RUN" --apply \
  --codex-bin /absolute/path/to/official/codex --limit 3
python3 "$CSD" status --run "$TASK_RUN" --verify-sources
```

Remove the limit to finish the existing plan. Mutations are sequential. `--transport proxy` can connect to a running official app-server control socket where supported; the default launches a dedicated official stdio app-server. Both validate `initialize.codexHome` against the run. Initialization may perform Codex's own metadata maintenance; the scripts themselves never write Codex SQLite.

The exact original file SHA-256 is verified before mutation and after archive. Parents are processed after children. Rejected or unconfirmed attempts stay held on subsequent runs. Source changes require a new snapshot and summary review, not a relaxed check.

## Multiple machines

For a workstation plus SSH development host:

1. Install the same Skill/scripts on each authorized host through the user's normal method.
2. Invoke the CLI on each host, for example through the existing `ssh my-devbox` connection. Choose a different run directory and label per host.
3. Distill and review each host's run. A coordinating agent may fetch the generated summaries for review, subject to the user's data-sharing scope.
4. Apply and verify on the machine that owns the source data. The machine fingerprint blocks applying a copied run to the wrong host.
5. Link the per-host `INDEX.md` files from a combined private index if useful. Do not copy authentication, browser cookies or entire Codex homes.

v0.1 does not provision SSH, synchronize native task databases, or transparently merge two hosts. It supports the same workflow independently on each host.

## Interrupted work and recovery

- An `apply.lock` prevents two runners from using the same run. After a crash, inspect the recorded PID, source host and journal. Remove only that stale lock after confirming its process is no longer running.
- A durable `intent` without a terminal event is checked against current source state. A matching completed mutation receives a reconciled receipt. An unconfirmed request is held and is not resent automatically.
- `api_error` records are retained even when readback proves a mutation succeeded. Active writers are kept. Other failures stop the runner for investigation.
- A concurrent archive by another application can trigger the unexpected-scope guard. Inspect it; do not silently expand the plan.
- Restore works only with verified archive receipts from the run:

```sh
python3 "$CSD" restore --run "$TASK_RUN" --session SESSION_UUID
python3 "$CSD" restore --run "$TASK_RUN" --session SESSION_UUID --apply \
  --codex-bin /absolute/path/to/official/codex
python3 "$CSD" status --run "$TASK_RUN" --verify-sources
```

For a parent, select its archived descendants too. An unresolved restore is not automatically retried. Original JSONL remains the recovery source; a summary alone cannot reconstruct a native task.
