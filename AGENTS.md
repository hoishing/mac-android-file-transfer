# Repository Guidelines

## Project Structure & Module Organization

This is a Python 3.12 CLI package for macOS Android file transfer.

- `src/maft/cli.py` contains the `maft` command implementation.
- `src/maft/__init__.py` exposes package metadata such as `__version__`.
- `tests/e2e/test_cli.py` covers CLI behavior through subprocess-based e2e tests.
- `README.md` documents user-facing install and usage instructions.
- `pyproject.toml` defines package metadata, the `maft` console script, linting, typing, and dev dependencies.
- `dist/` contains build artifacts and should not be edited by hand.

## Build, Test, and Development Commands

Use `uv` for all Python tooling.

- `uv sync --dev`: install the project and dev tools into `.venv`.
- `uv run maft doctor`: run the local CLI dependency check.
- `uv run ruff check .`: lint the repository.
- `uv run basedpyright`: run strict static type checking.
- `uv run pytest tests/e2e`: run the e2e test suite.
- `uv build`: build source and wheel distributions into `dist/`.
- `uv tool install --reinstall .`: reinstall the local `maft` command from this checkout.

## Coding Style & Naming Conventions

Use 4-space indentation, Python type annotations, and direct standard-library APIs where possible. Keep CLI errors actionable and route user-visible failures through `CliError`. Use `snake_case` for functions and variables, `PascalCase` for dataclasses and exceptions, and constants in `UPPER_SNAKE_CASE`.

Ruff is configured for 100-character lines and rules `E`, `F`, `I`, `B`, `UP`, `SIM`, and `RUF`. Basedpyright runs in strict mode over `src` and `tests`.

## Testing Guidelines

Do not add unit tests unless explicitly requested. Add or update e2e tests for feature changes, behavior changes, or UI/CLI surface changes. Remove stale e2e tests when the original feature is removed.

Name tests by behavior, for example `test_doctor_finds_go_installed_backend_outside_path`. Prefer subprocess tests that execute `python -m maft.cli` through the helper in `tests/e2e/test_cli.py`.

## Commit & Pull Request Guidelines

The current history uses concise imperative commit messages, for example `Create maft CLI for Android file transfer`. Keep commits focused and summarize the code change, not the process.

Pull requests should include a short behavior summary, any dependency or macOS requirements touched, and the exact verification commands run. For CLI output changes, include before/after examples.

## Release Workflow

For a version bump, edit `pyproject.toml` and package metadata as needed, commit all files with a message summarizing the code changes, push, build, and publish. Do not run tests during the version bump workflow unless explicitly instructed.
