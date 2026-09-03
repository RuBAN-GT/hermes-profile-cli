HELP_TEXT = """
Hermes Profile Manager

Paths
  manager config  YAML that lists locations. Default:
                  ~/.config/hermes-profile/config.yaml
  managed dir     Operational root for that location.
  profiles dir    One folder per profile. Each profile later has
                  config.yaml, .env, runtime files, and state/.
  fragments dir   Shared YAML and env snippets. profile.yaml only
                  stores relative references into this directory.

First-run setup
  Choose this computer or another machine over SSH.
  Set HERMES_PROFILE_CONFIG_DIR or pass --config to choose the manager config.
  Local setup lets you set config, managed, profiles, and fragments.
  Profiles/fragments follow the managed directory until you edit them.
  SSH uses your existing agent and keys. Passwords are not stored.

Locations
  local           Primary workspace from the manager config.
  local folders   Extra roots on this machine.
  SSH hosts       Same profile actions over SSH.

TUI keys
  ? / F1          Help
  enter           Open the selected location
  a               Add a location (locations screen) or Apply (workspace)
  e               Edit a location, including the primary local workspace
  d               Remove a location record (files stay on disk)
  i               Create remote dirs/config if missing
  n               New profile
  p               Preview rendered config (env values redacted)
  c               Reconcile runtime edits back into the profile
  r               Refresh
  esc             Back to locations
  q               Quit

CLI
  hermes-profile help
  hermes-profile init --managed-dir DIR
                  [--profiles-dir DIR --fragments-dir DIR]
  hermes-profile tui
  hermes-profile list
  hermes-profile create NAME
  hermes-profile status NAME
  hermes-profile render NAME
  hermes-profile reconcile NAME
  hermes-profile apply NAME
  hermes-profile ssh doctor|init|install HOST
  hermes-profile --host ALIAS list
  hermes-profile self-update
""".strip()
