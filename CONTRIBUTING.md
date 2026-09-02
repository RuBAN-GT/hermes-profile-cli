# Contributing

## Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/) for every
commit:

```text
type(scope): imperative summary
```

Use one of these types: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`,
`build`, `ci`, `chore`, `style`, or `revert`.

- Keep the summary concise, in imperative mood, and without a trailing period.
- Use a lowercase imperative verb after the colon: `feat: add clone install`.
- Add a scope only when it makes the affected area clearer:
  `fix(ssh): reject hermes agent binary`.
- Add a body for security changes, breaking changes, migrations, or non-obvious
  rationale. Wrap body lines at 72 characters.
- Use `!` and a `BREAKING CHANGE:` footer for incompatible changes.

## Versioning

This project follows [Semantic Versioning](https://semver.org/). The single
source of truth is `__version__` in `src/hermes_profile/__init__.py`.

Map Conventional Commits to the next version:

- `fix:` → patch (`0.1.0` → `0.1.1`)
- `feat:` → minor (`0.1.0` → `0.2.0`)
- `BREAKING CHANGE:` / `feat!:` → major (`0.1.0` → `1.0.0`)

When releasing:

1. Bump `__version__`.
2. Update `CHANGELOG.md`.
3. Commit with `chore(release): x.y.z`.
4. Tag `vx.y.z` and push the tag.

## Quality Checks

Run these checks before opening a pull request:

```bash
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```
