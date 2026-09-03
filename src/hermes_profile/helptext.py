from hermes_profile.i18n import language

HELP_TEXT_EN = """
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
  ctrl+t          Cycle Hermes themes
  ctrl+l          Switch EN/RU language
  ctrl+p          Command palette
  enter           Open the selected location
  a               Add a location (locations screen) or Apply (workspace)
  e               Edit a location, including the primary local workspace
  d               Remove a location, or delete the selected profile
  i               Create remote dirs/config if missing
  n               New profile
  p               Preview rendered config (env values redacted)
  f               Preflight: diffs and auth bindings, no writes
  u               Auth hub: map, bind, import, export, push, sync
  m               More: reconcile, discard runtime, backup, delete
  b               Backups
  c               Reconcile runtime edits back into the profile
  r               Refresh
  esc             Back to locations
  q               Quit

CLI
  hermes-profile help
  hermes-profile init --managed-dir DIR
                  [--profiles-dir DIR --fragments-dir DIR]
  hermes-profile tui
  hermes-profile mcp
  hermes-profile list
  hermes-profile create NAME [--share-from PROFILE]
  hermes-profile status NAME
  hermes-profile render NAME
  hermes-profile preflight NAME
  hermes-profile reconcile NAME
  hermes-profile apply NAME
   hermes-profile auth shared-status
   hermes-profile auth map-status
   hermes-profile auth bind NAME
   hermes-profile auth sources --from opencode|codex|hermes
   hermes-profile auth import --from ADAPTER --identity NAME
   hermes-profile auth export --to ADAPTER --identity NAME
   hermes-profile auth push --host HOST --identity NAME
   hermes-profile auth sync --from PROFILE --provider PROVIDER
  hermes-profile backup create|list
  hermes-profile backup restore NAME --confirm
  hermes-profile ssh doctor|init|install HOST
  hermes-profile --host ALIAS list
   hermes-profile self-update
""".strip()

HELP_TEXT_RU = """
Менеджер профилей Hermes

Пути
  конфиг менеджера  YAML со списком площадок. По умолчанию:
                    ~/.config/hermes-profile/config.yaml
  managed dir       Операционный корень площадки.
  profiles dir      Папка на каждый профиль: config.yaml, .env,
                    runtime и state/.
  fragments dir     Общие YAML и env. profile.yaml хранит только
                    относительные ссылки сюда.

Первый запуск
  Выберите этот компьютер или SSH-хост.
  HERMES_PROFILE_CONFIG_DIR или --config задают конфиг менеджера.
  Локально можно задать config, managed, profiles и fragments.
  SSH использует агент и ключи. Пароли не хранятся.

Площадки
  local           Основная локальная из конфига менеджера.
  local folders   Другие корни на этой машине.
  SSH hosts       Те же действия по SSH.

Клавиши TUI
  ? / F1          Справка
  ctrl+t          Сменить тему Hermes
  ctrl+l          Язык EN/RU
  ctrl+p          Палитра команд
  enter           Открыть площадку
  a               Добавить площадку или Apply
  e               Править площадку
  d               Убрать площадку или удалить профиль
  i               Создать удалённые каталоги/конфиг
  n               Новый профиль
  p               Просмотр конфига (env скрыт)
  f               Проверка: diff и привязки auth
  u               Auth: карта, bind, import, export, push, sync
  m               Ещё: свести, discard, бэкап, удалить
  b               Бэкапы
  c               Свести runtime в профиль
  r               Обновить
  esc             К площадкам
  q               Выход

CLI
  hermes-profile help
  hermes-profile init --managed-dir DIR
  hermes-profile tui
  hermes-profile mcp
  hermes-profile list
  hermes-profile create NAME [--share-from PROFILE]
  hermes-profile apply NAME
  hermes-profile auth import --from ADAPTER --identity NAME
  hermes-profile auth push --host HOST --identity NAME
  hermes-profile backup create|list
  hermes-profile self-update
""".strip()

HELP_TEXT = HELP_TEXT_EN


def help_text() -> str:
    return HELP_TEXT_RU if language() == "ru" else HELP_TEXT_EN
