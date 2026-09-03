# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/) and
[Conventional Commits](https://www.conventionalcommits.org/).

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
