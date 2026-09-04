# Storage and protocol compatibility

The portable release is developed for Python 3.11+ on macOS and Linux. Windows support is not claimed. The scripts have no third-party Python dependencies. They read Codex data through SQLite URI `mode=ro`; only Codex itself performs archive/restore mutations.

## Storage discovery

- Choose the highest numbered `state_*.sqlite` that has a supported `threads` table.
- Required columns: `id`, `rollout_path`, `archived`, `updated_at`, `source`.
- Optional metadata includes recency, pin, title, name, and working directory.
- Parent edges come from `source.subagent.thread_spawn.parent_thread_id`, plus `thread_spawn_edges` when its expected parent/child columns are present. Conflicting or unsupported edge schemas stop processing.
- `goals_*.sqlite` is read only when present. Unknown goal schemas stop planning instead of silently ignoring unfinished goals.
- Local global state supplies pins, queued follow-ups and heartbeat associations. Automation TOML supplies task targets. The coordinating agent should also provide current app-protected IDs.
- JSONL extraction supports user/assistant `response_item.message`, `event_msg.user_message`, `event_msg.agent_message`, task-completion messages, item-completed messages and historical agent messages. Mirror duplicates are folded within a turn. Parent links are retained; v0.1 does not remove inherited parent text across files.
- Unknown/invalid records, oversized lines, missing identity, compacted history and changed sources need coverage review. Recognized tool outputs, reasoning, system/developer messages and images are outside the text-summary scope.

Source files must resolve inside the selected Codex home. A run binds source SHA-256, extracted dialogue digest, host fingerprint and metadata. The digest detects accidental changes; it is not a signature against someone who can rewrite the run directory.

## Official interface

The transport pattern was exercised with Codex app-server 0.153.0 in the original operational workflow. The generalized release is validated with isolated synthetic stores and a mock server; this does not establish compatibility with every Codex build.

```text
codex app-server --listen stdio://
# Optional when a control socket already exists:
codex app-server proxy
```

Requests are newline-delimited JSON:

```json
{"id":1,"method":"initialize","params":{"clientInfo":{"name":"codex_session_distiller","version":"0.1.0"}}}
{"method":"initialized"}
{"id":2,"method":"thread/archive","params":{"threadId":"SESSION_UUID"}}
{"id":3,"method":"thread/unarchive","params":{"threadId":"SESSION_UUID"}}
```

The response to initialize must identify the expected Codex home. Unknown methods, interactive server requests, connection failures, active writer errors and unverified state changes do not authorize a fallback to direct storage edits.

On a new version, inspect the installed binary's `app-server --help` and generated protocol schemas before adapting the transport. `generate-json-schema --out /temporary/path` can expose the exact runtime contract where supported. Do not assume a wrapper named `codex` is the official binary.

Archiving generally relocates the raw rollout into archived storage while updating native state. The script trusts neither the expected filename nor a successful response alone: it reads the current DB path and verifies the original file's bytes. Parent archive can cascade to child tasks; refreshed descendant protection and child-first ordering are necessary.
