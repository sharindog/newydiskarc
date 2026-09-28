# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.0] - 2026-09-28

### Added
- Segmented downloads: files bigger than 4 MB are fetched by several connections at once
  (`--connections`/`-c`, default 4; "Соединений на файл" in the GUI). Segment progress is
  stored in `*.ydpart.state` for resuming; the whole file is verified with SHA-256 after
  assembly. Falls back to a single stream if the server ignores Range requests.
- Up to 64 files in parallel (`--threads`)

## [1.2.0] - 2026-09-28

### Changed
- **BREAKING**: `--update` was removed from `sync`: existing up-to-date files are always
  skipped, new and changed files are always downloaded
- `sync` writes `_metadata.json` only with `--nofiles` (and then only metadata)
- `full` saves single files under their original name instead of `dump.zip`
- Progress bars use rich (overall + per file); tqdm is no longer a dependency
- `-v` now really shows debug logs (including HTTP requests) without breaking progress bars

### Added
- Graphical interface: `ydiskarc gui` / `ydiskarc-gui`, with a download folder picker
- Downloading a single subfolder by its browser URL (percent-encoded or "pretty")
- SHA-256 (MD5 fallback) and size verification of every downloaded file, including resumed
  ones; `--verify` also checks files that already exist
- Temporary `*.ydpart` files, renamed only after verification
- Original modification dates for files and folders
- Parallel downloads (`--threads`), extra passes over failed files and folders (`--retries`)
- Summary at the end with the names of files and folders that failed; exit code 1 if any
- `--flat` (no folder tree) and `--safe-names` (Windows-compatible names), long path
  support on Windows, shortening of too long names
- Pagination of folder listings (folders with more than 1000 items)

### Fixed
- Files with quotes and with `%3F` in their names are downloaded
- HTTP errors are reported as warnings instead of being written into the downloaded file
- No hang after sleep mode: read timeouts, reconnects and refreshed download links
- No crash when the server does not respond
- The `ydiskarc.cmds` package was missing from the built distribution

## [1.0.1] - 2021-11-26

### Added
- First public release on PyPI and updated github code

## [1.1.0] - 2025-12-15

### Changed
- **BREAKING**: Changed URL from option (`--url`) to positional argument for `sync` and `full` commands
- Replaced Rich progress bars with tqdm for better compatibility and performance
- Made logging conditional - logs only appear when `--verbose` flag is used
- Changed default output filename for `full` command to `dump.zip` instead of auto-detected name

### Added
- **Progress bars with tqdm** - Visual progress indicators for file downloads
- **Pre-download statistics** - Shows total file count and estimated size before downloading
- **Examples directory** - Added example metadata file and comprehensive documentation
- **Metadata structure documentation** - Detailed documentation of Yandex.Disk API metadata format

### Fixed
- All linting issues (flake8, ruff, black, isort)
- Type annotation issues
- Code formatting and style consistency

## [Unreleased]

### Changed
- Migrated CLI framework from Click to Typer for better type hints and modern Python support
- Improved logging with better formatting and debug levels
- Enhanced error handling and user feedback
- **BREAKING**: Changed URL from option (`--url`) to positional argument for `sync` and `full` commands
- Replaced Rich progress bars with tqdm for better compatibility and performance
- Made logging conditional - logs only appear when `--verbose` flag is used
- Changed default output filename for `full` command to `dump.zip` instead of auto-detected name

### Added
- Rich library integration for better terminal output and progress indicators
- Verbose mode (`-v`/`--verbose`) for detailed logging
- Better error messages and exception handling
- Metadata extraction improvements
- **Progress bars with tqdm** - Visual progress indicators for file downloads
- **Pre-download statistics** - Shows total file count and estimated size before downloading
- **Examples directory** - Added example metadata file and comprehensive documentation
- **Metadata structure documentation** - Detailed documentation of Yandex.Disk API metadata format

### Fixed
- Security vulnerabilities in YAML loading
- CLI option conflicts
- Progress bar display issues
- Various code quality improvements
- All linting issues (flake8, ruff, black, isort)
- Type annotation issues
- Code formatting and style consistency
