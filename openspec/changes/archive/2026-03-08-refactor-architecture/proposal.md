                    # Proposal: Refactor Architecture and Modernize Tooling

## Goal
Improve the `ydiskarc` repository by modernizing the Python support, refactoring the core logic, enforcing strict CI/CD linting, and adopting performance enhancements.

## Why
Based on the repository analysis:
1. **End-of-Life Tooling**: The project officially supports Python 3.6, 3.7, and 3.8 which are EOL. The build system is split across `pyproject.toml`, `setup.py`, and `setup.cfg`.
2. **Architecture**: The `ydiskarc/cmds/processor.py` is over 800 lines long, violating the Single Responsibility Principle by mixing HTTP logic, file I/O operations, Yandex API details, and CLI wrappers. 
3. **CI/CD Quality**: The GitHub Actions CI pipeline allows formatting (`black`) and typing (`mypy`) checks to fail without breaking the build (`continue-on-error: true`).
4. **Performance Issues**: Downloads are sequential, which limits throughput on public resources with many small files.
5. **Safety**: Downloads stream directly to the final filename, potentially leaving corrupted files on interrupted or failed transfers.

## What Changes
1. **Update Python Constraints**: Bump the minimum supported Python version to 3.9+ and consolidate the build configuration into `pyproject.toml`.
2. **Architectural Splitting**:
   - `client.py`: For Yandex API specific requests and session handling.
   - `downloader.py`: For file layout, streaming, and progress bar logic.
   - `models.py`: Strongly typed dataclasses/pydantic models for Yandex.Disk API JSON responses.
3. **CI/CD Hardening**: Remove `continue-on-error: true` from `ci.yml` and add a pre-commit hook check.
4. **Safe Downloading**: Write active downloads to a `.part` suffix and rename upon successful completion.

## Alternatives Considered
- Keeping the existing `processor.py` intact and only adding new features. This was rejected because the current file size and mixed responsibilities make testing and extending difficult.
