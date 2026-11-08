# write-gate

**A deterministic pre-write content gate for Hermes Agent.**

Agents are good at producing files and bad at noticing that the file they just
produced is broken. `write-gate` inspects the *proposed* content of every file
write before it happens. If the content is invalid, the write is short-circuited
and Hermes returns the exact error to the model as a tool-call error — with line
and column, not a vague "something went wrong". The model then fixes the content
and retries **inside the same tool-calling loop**, with no human in the loop and
no wasted turn.

This is not a prompt, not a skill, and not a request to "please double-check".
It is an enforcement layer: the model cannot talk its way past it, cannot
forget it, and cannot be talked out of it by a long context.

## What it checks (all standard library, all deterministic)

| Check | Trigger | Error the model sees |
| --- | --- | --- |
| JSON syntax | `*.json` | `invalid JSON at line 7, column 3: Expecting property name enclosed in double quotes` |
| TOML syntax | `*.toml` | `invalid TOML: Expected '=' after a key in a key/value pair` |
| Python syntax | `*.py`, `*.pyi` | `invalid Python at line 12, column 9: invalid syntax` |
| NUL bytes | all files | `content contains NUL bytes` |
| Size cap | all files | `... exceeds the 2000000 byte safety cap` |
| User contracts | matching glob in `rules.json` | `missing required pattern ...` / `contains forbidden pattern ...` / `... exceeds max_lines` |

Contracts support `glob`, `syntax`, `max_lines`, `max_bytes`, `required[]` and
`forbidden[]` as regular expressions. See [`rules.example.json`](rules.example.json).
An invalid regex is reported as a rule error rather than crashing the agent.

## Patch / fragment edits

`patch`, `replace_in_file` and `edit_file` carry a *replacement fragment*, not a
whole file. A fragment such as `    return x` or `"a": 1,` is not a document, so
running a whole-file parser on it would produce a guaranteed false positive and
effectively switch off patching `.py`, `.json` and `.toml` files.

For those calls the gate:

- never runs whole-document parsers on the fragment (including `syntax` rules);
- still runs the NUL, size and regex/line/byte contract checks on the fragment;
- when `old_string` + `new_string` are present and the target file can be read,
  reconstructs the **post-edit** file in memory and parses that instead. A patch
  that would leave the file broken is still refused, with a `post-edit file
  would be invalid: ...` error.

Reconstruction is best-effort and read-only: if the file is missing, too large,
unreadable, or `old_string` is not found, the syntax check is skipped (fail
open) rather than blocking a legitimate edit. No file is written by the gate.

## Surfaces used

- native `plugin.yaml` + `register(ctx)`
- `ctx.register_tool()` → `write_gate_check` (explicit preflight the model can call)
- `ctx.register_hook("pre_tool_call")` → the veto
- `ctx.register_command()` → `/writegate`
- `ctx.get_config()` / `ctx.set_config()` → own settings namespace only
- `ctx.state` → profile-scoped counters
- `config_schema` → Settings → Plugins form in Hermes Desktop

No core overrides, no private Hermes imports, no monkeypatching.

## Install and use

```bash
hermes plugins install <owner>/hermes-write-gate
hermes plugins enable write-gate
```

```text
/writegate status          # on/off, mode, rules, counters
/writegate off             # disable the gate
/writegate observe         # report issues without blocking
/writegate enforce         # block bad writes (default)
/writegate rules /path/rules.json
```

The model can also preflight content itself:

```text
write_gate_check(path="config/app.json", content="...")
```

Settings are available in Desktop under Settings → Plugins → write-gate:
`enabled` (bool), `mode` (`enforce` / `observe`), `rules_file` (string).

## Demo (90 seconds)

1. `/writegate status` — gate is on, mode `enforce`.
2. Ask Hermes: *"create `config/app.json` with name, version and retries"*.
   Inject a trailing comma into the content the model proposes.
3. Hermes shows: `[write-gate] refused to write config/app.json` plus the exact
   line/column. The file does not exist on disk.
4. The model immediately retries with valid JSON. `write-gate` lets it through.
5. `/writegate status` — `checked=2 blocked=1 passed=1`.
6. `cat config/app.json` — valid, parseable JSON.

The whole loop happens in one turn, with no user intervention.

## Why this beats a skill or a prompt

A skill says "remember to validate your output". It occupies context, competes
with the rest of the system prompt, and is advisory. `write-gate` runs a real
parser on the exact bytes about to be written, on every write, and the failure
is delivered as a tool error that the tool-calling loop must handle. It also
costs zero tokens when nothing is wrong.

## Reliability

- Zero Python dependencies: `json`, `tomllib`, `ast`, `re`, `fnmatch`, `pathlib`.
- Every hook callback accepts `**kwargs` and is wrapped in `try/except`.
- The gate fails **open**: if anything in the plugin breaks, Hermes carries on.
- `checkers.py` has no Hermes imports and is unit-tested independently:
  `python -m unittest discover -s tests -v` (51 tests, including patch/fragment
  regression coverage).
- Validated with `hermes plugins validate . --install-deps` and
  `hermes plugins doctor . --ci`.

## Disclosures (catalog rule 13)

- **Network calls to third-party services:** none. The plugin makes no network
  calls of any kind.
- **Reads outside the plugin's own data:** an optional, user-specified JSON
  rules file named in the `rules_file` setting, and — for fragment edits only —
  the target file named in the tool call's own `path` argument, read once (size
  capped at 2 MB) to reconstruct the post-edit file for a syntax check. That
  read is best-effort and never writes. Plugin code otherwise reads only the
  in-memory arguments of the tool call it is gating. It never reads `.env`,
  `auth.json`, credential stores, other profiles or session databases.
- **Shell commands:** none. No subprocesses are started.
- **Long-running background processes:** none.
- **Stored credentials:** none. No secrets are read, written or forwarded.
- **Telemetry / usage reporting:** none, opt-in or otherwise.
- **State written:** three integer counters in the profile-scoped plugin state
  (`HERMES_HOME/plugin-data/write-gate/`). No file contents, paths, tool
  arguments or model text are persisted.
- **Approvals:** the plugin never auto-approves anything. In `enforce` mode it
  uses `pre_tool_call`'s documented `block` directive; it never escalates,
  bypasses or disables Hermes's own approval gate.

## Catalog submission

The catalog entry is a single YAML file added to
`NousResearch/hermes-agent/plugin-catalog/`, pinned to a full 40-character
commit SHA. See [`catalog-entry.example.yaml`](catalog-entry.example.yaml).

```bash
hermes plugins validate . --install-deps
hermes plugins doctor . --ci
```

## License

MIT
