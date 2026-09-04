# Summarizer adapter contract

`distill --command-json '["executable","arg",...]'` executes an argv array with `shell=False`. It sends one complete UTF-8 prompt to stdin and expects one JSON object on stdout. The process must be a text-only transformation with execution tools disabled. Use an existing model provider authorized for the transcript's data.

The prompt contains `INPUT` followed by:

```json
{
  "session_id": "00000000-0000-4000-8000-000000000001",
  "unit": "map-0",
  "input_digest": "SHA256_OF_DATA",
  "data": [{"role": "user", "text": "A synthetic request", "line": 3, "char_offset": 0}]
}
```

Return the exact session ID, unit and input digest. All six sections are required. Each claim must have nonempty text and at least one original source line available in this input; unsupported inferences belong outside the summary.

```json
{
  "session_id": "00000000-0000-4000-8000-000000000001",
  "unit": "map-0",
  "input_digest": "SHA256_OF_DATA",
  "sections": {
    "goal": [{"text": "The user requested an investigation.", "lines": [3]}],
    "outcome": [],
    "decisions": [],
    "evidence": [],
    "unresolved": [],
    "next_steps": []
  }
}
```

At least one claim is required. Empty sections are allowed; do not fabricate missing outcomes or next steps. For reductions, `data` contains previous unit summaries and citations still refer to the original dialogue. Pairwise reduction retains all map outputs alongside the final brief. Intermediate outputs are cached by the whole request digest.

The runner also accepts a CLI JSON wrapper with `structured_output`, or a `result` string containing the object. Claude Code with `--output-format json` can use this form. Plain Markdown or code-fenced JSON is rejected, not heuristically repaired. A provider-specific adapter can enforce a JSON schema if needed.

The default input limit is 16,000 text characters per map unit. A single larger message is split with source line and character offset retained. The default command timeout is 180 seconds. Failed output is written under the private run's `model-errors/`; it is never marked as reviewed. Existing successful units are reused on the next invocation.

Redaction removes common token/key, authorization, Cookie and inline binary patterns before model input. It is not a guarantee that arbitrary credentials, identifying paths or business information are absent. Inspect the extracted corpus and use a suitable model. Summarizer execution may contact that provider even though orchestration and files are local.
