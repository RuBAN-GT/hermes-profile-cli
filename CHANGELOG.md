# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/) and
[Conventional Commits](https://www.conventionalcommits.org/).

## [0.7.0] - 2026-09-04

### Added

- `create --share-from` copies shared fragment refs and writes a new identity
  stub without copying secrets.
- `create --add-config` / `--add-env` seed fragment refs on a new profile.
- `update --set-config` / `--set-env` replace fragment lists.
- `hermes-profile mcp` stdio server for local and SSH locations. Env tools
  return keys only.

## [0.6.0] - 2026-09-04

### Added

- TUI language switch EN/RU via `ctrl+l`, persisted as `ui.language`.

## [0.5.0] - 2026-09-04

### Added

- TUI auth hub covers map status, bind, import, export, push, sources, and
  shared status in addition to sync.
- TUI More menu: discard-runtime apply, backups, and profile delete.
- `ctrl+t` cycles only Hermes themes.

### Fixed

- Theme switching used hardcoded Dracula colors and Textual builtin palettes,
  so Nord/Gruvbox did not apply. Screens now use theme variables, and builtin
  themes are unregistered.

## [0.4.0] - 2026-09-04

### Added

- `fragments/auth-map.yaml` binds profiles to named identity stores or shared
  providers without storing tokens.
- `apply` / `auth bind` attach an identity by moving the live store into the
  profile and leaving a pointer under `<root>/identities/`.
- Generic auth adapters for OpenCode, Codex CLI, and Hermes stores.
- `auth map-status`, `auth sources`, `auth import`, `auth export`, and
  `auth push --host` copy selected provider slices, including over SSH.

### Changed

- Help and README cover auth-map bindings and adapter transfers.

## [0.3.0] - 2026-09-03

### Added

- `preflight` shows an effective config diff and a file materialization diff.
- TUI Preflight (`f`) and Auth (`u`) actions.
- Provider-selective `auth sync` into the Hermes root fallback store.
- `auth shared-status` reports shared providers without exposing secrets.
- `backup create|list|restore` for fragments and profile declarations.

### Changed

- `apply` sorts top-level YAML keys in generated `config.yaml`.
- Help and README cover preflight, shared auth, and setup backups.

## [0.2.0] - 2026-09-03

### Added

- First-run TUI asks local vs SSH, then shows only those fields.
- Local init can set manager config, managed, profiles, and fragments paths.
- Field hints on setup screens and `?` / `F1` TUI help.
- `hermes-profile help` prints the same guide from the CLI.
- `hermes-profile self-update` upgrades this CLI from git.
- Loading animation while profile and SSH actions run.
- Preview shows a table of top-level config keys.

### Changed

- First-run setup no longer mixes local and SSH fields on one screen.

## [0.1.0] - 2026-09-03

### Added

- Profile manager CLI and TUI for local and SSH Hermes locations.
- Remote host setup that creates managed directories and config over SSH.
- Clone-based remote CLI install from this GitHub repository.
- Rejection of the Hermes agent binary in the remote manager CLI field.
