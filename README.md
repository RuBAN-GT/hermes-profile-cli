# Hermes Profile CLI

Declarative TUI/CLI manager for isolated Hermes agent profiles over local paths
and SSH.

`hermes-profile` materializes isolated Hermes profiles from shared YAML and
environment fragments. It is deliberately separate from Hermes plugins: profile
selection and file generation happen before a gateway process starts.

The tool does not create, restart, or delete `launchd` services. A privileged
controller may invoke `hermes-profile apply <profile>` before operating a
profile-specific service.

## Requirements

- Python 3.11 or newer (`python3 --version`)
- `git` (clone and remote install)
- `ssh` on the manager machine for remote hosts

macOS often ships `python3` 3.9. If `python3 --version` is below 3.11:

```bash
brew install python@3.12
```

Then use `python3.12` in the commands below.

## Setup

### Local install

```bash
git clone https://github.com/RuBAN-GT/hermes-profile-cli.git
cd hermes-profile-cli
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/hermes-profile --version
```

The CLI is `.venv/bin/hermes-profile`. Put that directory on `PATH`, or symlink
it:

```bash
mkdir -p ~/.local/bin
ln -sf "$(pwd)/.venv/bin/hermes-profile" ~/.local/bin/hermes-profile
```

This is **not** the Hermes agent (`hermes`). Do not point `remote_binary` at
`hermes`.

### First run

```bash
hermes-profile tui
```

It first asks **this computer** or **SSH**, then lets you set manager config,
managed, profiles, and fragments paths. Press `?` or `F1` for help. For
non-interactive setup:

```bash
hermes-profile init --managed-dir /absolute/path/to/hermes-managed
hermes-profile init --managed-dir /srv/hermes/managed \
  --profiles-dir /srv/hermes/homes \
  --fragments-dir /srv/hermes/fragments
hermes-profile help
```

Copy `config.example.yaml` outside the repository if you want a hand-written
config, then set `HERMES_PROFILE_CONFIG` or pass `--config /path/to/config.yaml`.

### Remote host

The remote machine also needs **git** and **Python 3.11+**. Install Python first
if needed (`brew install python@3.12` on macOS). Non-interactive SSH must see
that interpreter: Homebrew is checked at `/opt/homebrew/bin` and
`/usr/local/bin`.

From the manager:

```bash
hermes-profile ssh install gateway-a
```

Or in the TUI: **Clone + install**. That creates remote dirs/config, clones this
repository, and installs the CLI. **Init dirs** / `ssh init` only create
dirs/config.

Remote layout:

```text
~/.local/share/hermes-profile/src    # git clone
~/.local/share/hermes-profile/venv   # venv
~/.local/share/hermes-profile/venv/bin/hermes-profile
```

The first-run TUI can do the same after you choose SSH: **Create SSH setup,
clone, and install CLI**. Use an SSH target such as
`deploy@gateway.example -p 22`. The remote manager CLI field is
`hermes-profile`, not the `hermes` agent.

Update this CLI (git fetch of `main`, then reinstall into the current Python):

```bash
hermes-profile self-update
```

## Layout

`managed_dir` is the tool's state root. `profiles_dir` and `fragments_dir`
default to its `profiles/` and `fragments/` children, but may be configured
independently for an existing installation.

```text
<profiles_dir>/<profile>/
  profile.yaml
  config.yaml
  .env
  runtime-config.yaml
  runtime.env
  state/
    applied-config.yaml
    applied.env
```

`profile.yaml` contains only fragment references relative to `fragments_dir`:

```yaml
config:
  - config/base.yaml
  - config/telegram.yaml
env:
  - env/common.env
  - env/tyrion.private.env
```

Fragments and actual profile state are local operational data and are not
created or tracked by this project.

## Commands

```bash
hermes-profile list
hermes-profile create tyrion
hermes-profile show tyrion
hermes-profile status tyrion
hermes-profile render tyrion --check
hermes-profile reconcile tyrion
hermes-profile apply tyrion
hermes-profile delete tyrion --confirm
hermes-profile tui
hermes-profile help
hermes-profile self-update
```

`apply` refuses to overwrite `config.yaml` or `.env` when they differ from the
last applied snapshot. This catches changes made by Hermes through `hermes
config`, a dashboard, or a plugin. Run `reconcile` to preserve additive and
changed values in the profile-local runtime overlay. Key deletion is not yet a
supported merge operation.

## Merge and secrets

YAML maps merge recursively; lists and scalar values are replaced by the later
fragment. Environment fragments accept only comments, blank lines, and
`NAME=value` assignments. They are never sourced or executed.

The tool writes `.env`, `runtime.env`, and state environment snapshots as mode
`0600`. It does not print environment values. Auth-pool transfer is intentionally
not implemented yet: it must use Hermes' auth-store lock and validation APIs,
rather than copy `auth.json` as a file.

## Remote hosts

Without `--host`, commands manage local files. Define a host in the manager
configuration to send the same command to a remote installation through your
system SSH client:

```bash
hermes-profile ssh doctor gateway-a
hermes-profile ssh init gateway-a
hermes-profile ssh install gateway-a
hermes-profile --host gateway-a list
hermes-profile --host gateway-a apply tyrion
```

`ssh init` creates only configured profile/fragment directories and a
secret-free remote manager config when it does not exist. It does not install
this package, copy profiles, credentials, `.env` files, or create services.
`ssh install` runs `ssh init`, then clones this repository on the remote host
and installs `hermes-profile` into `~/.local/share/hermes-profile/venv`. The
remote needs `git` and Python 3.11+. List, status, and Preview work over SSH
files without that binary. Apply/Reconcile need it. It does not generate Hermes
`bin/` (uv, tirith) inside a profile home.

The TUI starts on a location picker: `local`, extra local folders, and every
configured SSH host. Enter opens that workspace. Escape returns to locations.
Remote calls run in background workers; they use JSON protocol rather than
parse terminal output.

Use **Add location** in the TUI to add either another local profile root or an
SSH host. Saving a local location creates its `managed`, `profiles`, and
`fragments` directories with mode `0700`. For SSH, **Save host** only records
the connection; **Init dirs** also creates remote manager directories and its
secret-free config; **Clone + install** clones this repo and installs the
remote `hermes-profile` CLI.

The TUI includes curated `hermes-dracula`, `hermes-nord`, and `hermes-gruvbox`
themes in the Textual command palette. The selected curated theme is persisted
as `ui.theme` in the manager config.

## Hermes-owned auth

Hermes owns `auth.json`. The manager never merges, snapshots, or displays its
credentials. `status` tracks a digest of credential inventory only (provider,
credential ID, auth type, source), so new or removed credentials are visible
without exposing secrets. Token refreshes and request counters do not trigger
inventory drift. Run `reconcile` to acknowledge the current inventory; it never
modifies `auth.json`. Auth inventory does not block config/env `apply`.

## Quality checks

```bash
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```
