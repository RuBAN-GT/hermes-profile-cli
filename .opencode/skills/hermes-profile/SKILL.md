---
name: hermes-profile
description: Manage configuration within a Hermes agent profile using hermes-profile CLI locally or over SSH. Use for Hermes profile config.yaml, .env, profile.yaml, fragment decomposition, and runtime drift; not for unrelated application configs.
---

# Hermes Profile Management

Use this workflow for Hermes profile work on a local filesystem or an SSH host.
Treat the profile manager as declarative configuration management, not as a
tool for copying live files between directories.

Scope: configure the requested Hermes profile using this repository's CLI.
Do not install or upgrade the CLI, restart agents, change authentication
bindings, or modify unrelated profiles unless requested. Applying configuration
does not restart agents.

Read this repository's `docs/guide.md` and relevant implementation when exact
behavior matters. Check the target CLI's `--version` and command `--help`;
a remote checkout may differ from the local version. Do not confuse the
`hermes-profile` manager with the separate `hermes` agent command.

## Configuration Model

Each location has three independent roots:

- `managed_dir`: manager-owned data such as fragments and backups.
- `profiles_dir`: one runtime home per profile, usually `<root>/profiles/<name>`.
- `fragments_dir`: declarative YAML and env inputs, usually
  `<managed_dir>/fragments`.

The declaration `<profiles_dir>/<name>/profile.yaml` lists fragment paths
relative to `fragments_dir`:

```yaml
config:
  - config/common.yaml
  - config/host.yaml
  - config/capabilities/browser.yaml
  - config/profiles/example.yaml
env:
  - env/common.env
  - env/terminal.env
  - env/profiles/example.private.env
```

`hermes-profile apply NAME` merges these fragments plus any runtime overlays
and materializes the profile configuration and snapshots:

```text
<profiles_dir>/<name>/config.yaml
<profiles_dir>/<name>/.env
<profiles_dir>/<name>/state/applied-config.yaml
<profiles_dir>/<name>/state/applied.env
```

The runtime `config.yaml` and `.env` are evidence of the currently effective
state, not the desired place for durable edits. The root-level `<root>/.env`
is not managed by `hermes-profile`; never overwrite it to synchronize a
profile.

`<managed_dir>/config.yaml` can be a legacy Hermes runtime overlay. Do not
mistake it for the profile manager configuration. A manager configuration must
contain `managed_dir` and normally also records `profiles_dir` and
`fragments_dir`.

## Discovery First

Before editing, establish the location and the three roots. The user may give
either a local path or SSH connection details.

1. Find the CLI binary: prefer an explicitly supplied path, then
   `hermes-profile` in `PATH`, then a repository virtual environment such as
   `<checkout>/.venv/bin/hermes-profile`.
2. Find the manager config: use an explicit `--config` path when supplied or
   `$HERMES_PROFILE_CONFIG_DIR/config.yaml` when that override is configured;
   otherwise check `~/.config/hermes-profile/config.yaml`. Validate that it has
   a string `managed_dir` value. Resolve defaults using the target CLI rather
   than assuming the profiles are under `managed_dir`.
3. Read the manager config and profile declaration. Inventory `profiles_dir`,
   `fragments_dir`, `<profile>/profile.yaml`, existing fragment files, runtime
   overlays, and `state/applied-*`.
4. Use `render`, `preflight`, and `status` before writing when a valid manager
   config is available.

If the manager config is missing but the three roots are unambiguous, an
ephemeral manager config may be used only for read-only rendering, preflight,
or a user-approved apply. Never replace a legacy managed overlay to make the
CLI work.

If the roots or target profile cannot be determined unambiguously, stop before
making changes and ask one concise question. State exactly what is missing and
offer the smallest useful choices, for example:

> I found profile homes but no manager configuration. Which paths should I use
> for `profiles_dir` and `fragments_dir`?

Do not ask this question when the canonical layout makes the roots clear.

Also ask when the target profile, connection details, CLI path, or intended
source of truth remains unclear after a bounded read-only discovery. Do not
scan unrelated directories or initialize a new layout to resolve ambiguity.
An active profile marker alone is not permission to modify that profile.

For a configured SSH location, use the local manager's `--host ALIAS` and
its recorded remote binary/config. Alternatively, use the supplied SSH host,
user, port, and explicit remote CLI/config paths. Keep those execution modes
distinct: local paths do not become remote paths automatically. A CLI missing
from remote `PATH` does not mean it is absent; check a supplied checkout's
`.venv/bin/hermes-profile` before asking for its location.

Use the same verified CLI, manager config, and location for every command.
If an ephemeral config is necessary, create it with restricted permissions,
validate all three roots first, and remove only that temporary file afterward.

## Analyze Runtime Against Declarations

For normal configuration changes, edit declarations. When the user explicitly
names the live profile as the source of truth, migrate its current state into
fragments without changing behavior. Do not silently choose between conflicting
runtime and declared values when the requested direction is unclear.

1. Parse the declared env fragments and compare their keys and values to the
   runtime `.env`. Do not display secret values, tokens, or passwords.
2. Merge declared YAML fragments in their listed order and compare the result
   recursively with runtime `config.yaml`.
3. Inspect `runtime.env` and `runtime-config.yaml` when present; they may be
   intentional drift previously preserved by `reconcile`.
   Compare both the fragment-only result and the effective result including
   overlays; a clean snapshot alone does not prove declarations match runtime.
4. Report only variable names, YAML paths, and fragment filenames. Explain
   whether each difference is missing declaration, stale fragment value, or
   an intentional runtime overlay.

Choose the narrowest correct destination for each durable value:

| Value scope | Destination |
| --- | --- |
| Shared policy or defaults | `config/common.yaml` or `env/common.env` |
| Host-specific behavior | `config/host.yaml` |
| Reusable optional integration | `config/capabilities/<name>.yaml` |
| One profile's policy, paths, volume mounts, or forwarding list | `config/profiles/<name>.yaml` |
| Per-profile secrets, tokens, and private paths | `env/profiles/<name>.private.env` |

Environment fragments contain only `NAME=value` assignments, comments, and
blank lines. They are never shell scripts. Later fragments override earlier
keys; YAML maps merge recursively, while YAML lists and scalars replace the
earlier value. When changing a list such as `terminal.docker_forward_env`,
declare the complete desired list in the latest applicable fragment.

Fragment YAML string values and env values expand `${VAR}` once, without shell
evaluation or rescanning inserted text. Escape existing literal `${VAR}` as
`$${VAR}` when importing runtime into fragments. Keys and YAML types do not
expand. Env resolves first against one process environment snapshot plus earlier
assignments, including earlier lines in the same fragment; last assignment wins.
`TOKEN=${TOKEN}` uses the process or previous value, not a recursive reference.
YAML uses final env including `runtime.env`. Runtime env/YAML overlays stay literal;
fragment-only rendering ignores both overlays.

Lookup-only fallbacks: `HERMES_PROFILE` is the profile name;
`HERMES_PROFILE_DIR`, `HERMES_PROFILES_DIR`, `HERMES_FRAGMENTS_DIR`, and
`HERMES_MANAGED_DIR` come from configured roots. Process values, including empty
ones, take precedence; env fragments can override them. Do not auto-set
`HERMES_HOME` or emit defaults into `.env` without explicit assignments.
Missing variables and CR/LF/NUL in expanded env values fail with value-free errors.

Preview APIs redact substituted YAML paths on both sides of preflight diffs;
affected lists are hidden completely to protect old/reordered entries. Computed
fallbacks and unrelated config remain visible. Apply writes actual values and
retains historical paths (not values) in `state/interpolation.yaml`; preserve this
file. Changed redacted values use `<redacted: changed>` in preflight; equal values
produce no diff. Hardcoded secrets and old substitutions without metadata are not
automatically protected. Verify the remote CLI supports this behavior before
relying on SSH previews.

Preserve list order, value types, and existing settings when importing runtime.
Detect removed keys as well as additions and changes: `reconcile` does not
support deleting keys. Do not assume it produces an exact runtime migration.

Reuse existing fragments rather than creating one file per setting. Before
editing a shared fragment, inspect every profile referencing it and verify the
change is intended for all of them. Otherwise use a profile-specific override.
Never copy secrets between profiles or classify a secret as shared merely
because two profiles currently contain the same value.

## Safe Change Procedure

1. Present the proposed fragment changes and their scope before writing when
   the user asked for analysis or a proposal first.
2. Back up changed declarations and any runtime/state files that will be
   overwritten, using restricted permissions. Backups must preserve rollback
   data without exposing secrets in chat, logs, or version control.
3. Edit fragments and, only if required, update the profile's ordered fragment
   references in `profile.yaml`.
4. Run `hermes-profile preflight NAME` and review both configuration and env
   diffs by names/paths only.
   Validate YAML and env syntax before apply. Capture/redact CLI output when
    YAML can contain hardcoded credentials; interpolation redaction is not a
    general secret detector.
   Stop on unexpected diffs. For an exact runtime import, require semantic
   equality of config and env before apply, including list order.
5. If current runtime files differ from `state/applied-*`, explain the drift.
   Use `reconcile NAME` to preserve unmanaged additions. Use
   `apply NAME --discard-runtime` only when the desired declarations now fully
   represent the intended runtime state and the user has approved replacing
    the runtime overlay.
   Recheck runtime immediately before applying so concurrent dashboard or agent
   changes are not discarded. Never use `--discard-runtime` merely to bypass a
   drift error, and do not manually rewrite snapshots to hide drift.
6. Run `apply NAME`, then verify `preflight NAME` has no effective changes and
    `status NAME` reports clean config and env drift.

Prefer individual profile applies over `apply-all`. Bulk apply is not atomic.
Verify any other profiles affected by shared fragments; report auth inventory
changes separately rather than changing auth to make status appear clean.

For SSH, run the same discovery and verification on the remote host. Never
copy a profile's `.env` into a root `.env`, and never print environment values
in command output or the final report.

## Final Report

State the profile(s) handled, changed fragment paths, variable names and YAML
paths only, the CLI command outcome, and final `preflight`/`status` result.
Mention backup locations without exposing their contents.
