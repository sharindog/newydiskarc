# ydiskarc

ydiskarc (pronounced *Ai-disk-arc*) is a command line tool used to backup Yandex.Disk public resources.
Public resources are only shared files and folders from Yandex.Disk service.
Yandex provides free-to-use API that allow to download the data.

## Main features

* **Always incremental** - files that are already downloaded and up to date are skipped,
  new and changed files are downloaded (there is no `--update` flag any more)
* **Safe downloads** - data goes to a temporary `*.ydpart` file which is renamed only after
  the size and SHA-256 (MD5 as a fallback) reported by Yandex.Disk have been checked
* **Resume support** - interrupted downloads continue from the last byte, and the resumed
  file is verified as a whole
* **Errors are warnings, not files** - an HTTP error is never written into the file being
  downloaded; a warning is printed and the file is retried
* **Resilient** - network errors, dead connections (e.g. after sleep mode), missing server
  responses and rate limiting are retried; failed files and folders get extra passes
* **Subfolders** - download only one folder by giving its URL from the browser
  (`https://disk.yandex.ru/d/KEY/Folder/Subfolder`, percent-encoded or "pretty")
* **Any file names** - quotes, `%3F` and other odd characters in names are supported;
  `--safe-names` makes names valid on Windows/FAT/NTFS, long names are shortened
* **Original dates** - files and folders get their modification time from Yandex.Disk
* **Parallel downloads** - several files at once (`--threads`), and big files in segments
  over several connections (`--connections`), which matters because Yandex.Disk limits the
  speed of a single connection
* **Progress and statistics** - overall and per-file progress bars, and a summary at the end
  including the names of files that could not be downloaded
* **Graphical interface** - `ydiskarc gui` / `ydiskarc-gui`, with a folder picker
* **Metadata only mode** - `--nofiles` saves `_metadata.json` for every folder

## Installation

### Any OS

A universal installation method (that works on Windows, Mac OS X, Linux, …,
and always provides the latest version) is to use pip:

```bash
# Make sure we have an up-to-date version of pip and setuptools:
$ pip install --upgrade pip setuptools

$ pip install --upgrade ydiskarc
```

(If ``pip`` installation fails for some reason, you can try
``easy_install ydiskarc`` as a fallback.)

### Python version

Python version 3.9 or greater is required.

## Graphical interface

```bash
$ ydiskarc gui
# or
$ ydiskarc-gui
```

Paste a public link (to the whole share or to a subfolder), choose the folder to save into
with "Обзор…" and press "Скачать". Downloads can be stopped at any time and resumed later by
pressing "Скачать" again. The GUI uses `tkinter`, which is included in the Python installers
for Windows and macOS; on Linux install it with your package manager
(e.g. `apt install python3-tk`).

## Usage

Synopsis:

```bash
$ ydiskarc [command] [URL] [flags]
```

**Examples:**
```bash
# Sync command - URL is a positional argument
$ ydiskarc sync https://disk.yandex.ru/d/ABC123 -o output

# Full command - URL is a positional argument  
$ ydiskarc full https://disk.yandex.ru/i/XYZ789 -o output
```

See also ``python -m ydiskarc`` and ``ydiskarc [command] --help`` for help for each command.

## Commands

### Sync command

Synchronizes files from a public folder to a local directory, keeping the folder tree.
Running it again only downloads what is new or changed, so it is safe to re-run after an
interruption or to update an existing backup.

**Basic usage:**
```bash
$ ydiskarc sync https://disk.yandex.ru/d/VVNMYpZtWtST9Q -o mos9maystyle
```

**Only one subfolder (URL copied from the browser):**
```bash
$ ydiskarc sync "https://disk.yandex.ru/d/VVNMYpZtWtST9Q/Folder/Subfolder" -o subfolder
```

**Metadata only (no file downloads):**
```bash
$ ydiskarc sync https://disk.yandex.ru/d/VVNMYpZtWtST9Q -o mos9maystyle --nofiles
```

**Options:**
- `URL` - Public folder URL, or URL of a subfolder (required, positional argument)
- `--output`, `-o` - Output directory (defaults to resource ID or the subfolder name)
- `--nofiles`, `-n` - Metadata-only mode: save `_metadata.json` for every folder and no files.
  Without this flag no metadata is written.
- `--threads`, `-t` - Number of files downloaded in parallel (default: 3, up to 64)
- `--connections`, `-c` - Connections per file (default: 4, up to 16). Files bigger than
  4 MB are split into segments (2-16 MB) that are downloaded in parallel; progress is kept in
  `*.ydpart.state`, so an interrupted file continues where it stopped. The total number of
  connections is up to `threads × connections`.
- `--retries`, `-r` - Extra passes over files and folders that failed (default: 3)
- `--flat` - Save all files directly into the output folder, without the folder tree
  (for shares with paths too long for the file system)
- `--safe-names` / `--native-names` - Replace characters not allowed on Windows/FAT/NTFS
  (`" ? * : < > |`) with similar looking ones (default: on for Windows)
- `--verify` - Check the SHA-256 of files that already exist locally even if their size and
  date match (slow for big backups)
- `--verbose`, `-v` - Enable verbose logging, including HTTP requests

**How existing files are handled:** a local file is considered up to date when its size and
modification date match Yandex.Disk. If only the size matches, its checksum is compared (and
the date is fixed if the content is right). Otherwise the file is downloaded again.
Partially downloaded `*.ydpart` files are resumed.

The exit code is `0` when everything was downloaded, `1` if some files or folders failed
(they are listed in the summary) and `130` when stopped with Ctrl+C.

### Full command

Downloads single file or directory. Single files are downloaded with their original format.
Directories are downloaded as ZIP files containing all files inside.

**Basic usage:**
```bash
$ ydiskarc full https://disk.yandex.ru/i/t_pNaarK8UJ-bQ -o files
```

**With metadata:**
```bash
$ ydiskarc full https://disk.yandex.ru/i/t_pNaarK8UJ-bQ -o files -m
```

**Verbose output:**
```bash
$ ydiskarc full https://disk.yandex.ru/i/t_pNaarK8UJ-bQ -o files -v -m
```

**Options:**
- `URL` - Public resource URL (required, positional argument)
- `--output`, `-o` - Output directory
- `--filename`, `-f` - Output filename (original name for a file, `dump.zip` for a folder)
- `--metadata`, `-m` - Extract and save metadata as `_metadata.json`
- `--safe-names` / `--native-names` - see the `sync` command
- `--verbose`, `-v` - Enable verbose logging (logs are hidden by default)

**Note:** 
- Single files are downloaded with their original name, verified and get their original date
- Directories are downloaded as ZIP files (default filename: `dump.zip`)
- The command displays file count and size information before downloading

### Version command

Check the installed version:
```bash
$ ydiskarc version
```

## Configuration

ydiskarc can be configured using a `.ydiskarc` YAML file in your project directory.

**Example configuration:**
```yaml
keys:
  yandex_oauth: your_oauth_key_here
```

To configure:
```bash
$ ydiskarc configure --key YOUR_OAUTH_KEY
```

## Troubleshooting

### Common Issues

**"No download url. Probably wrong public url/key?"**
- Verify the URL is correct and the resource is publicly accessible
- Check that the URL format matches: `https://disk.yandex.ru/d/...` or `https://disk.yandex.ru/i/...`

**"Failed to download file" or network errors**
- Check your internet connection
- The tool automatically retries on transient failures
- For rate limiting, the tool will wait and retry automatically

**"Failed to create directory"**
- Check file system permissions
- Ensure you have write access to the output directory

**Resume interrupted downloads**
- Just run the same command again: finished files are skipped, `*.ydpart` files are resumed
  from the last byte and verified

**Some files were not downloaded**
- Their names and the reasons are printed at the end; run the command again to retry them

**Path too long (Windows)**
- Long paths are supported via the `\\?\` prefix; if the file system still refuses, use
  `--flat`

### Verbose Mode

By default, ydiskarc runs quietly and only shows progress bars and essential information. For detailed debugging information, use the `--verbose` or `-v` flag:
```bash
$ ydiskarc sync https://disk.yandex.ru/d/... -o output -v
```

When verbose mode is enabled, you'll see:
- Detailed logging of all operations
- File-by-file download progress
- API request details
- Error stack traces

## Examples

**Download a public folder:**
```bash
$ ydiskarc sync https://disk.yandex.ru/d/ABC123 -o my_backup
```

**Download a single file with metadata:**
```bash
$ ydiskarc full https://disk.yandex.ru/i/XYZ789 -o downloads -m
```

**Update existing backup (only new and changed files are downloaded):**
```bash
$ ydiskarc sync https://disk.yandex.ru/d/ABC123 -o my_backup
```

**Get metadata only:**
```bash
$ ydiskarc sync https://disk.yandex.ru/d/ABC123 -o metadata_only --nofiles
```

**Example output:**
```
Total: 4 files (4.77 MB) in 3 folders; to download: 4 files (4.77 MB)
Failed to download a.txt: server returned HTTP 500
Retry 1/3: 1 file(s), 0 folder(s) in 15 s

Folders: 3, files: 4 (4.77 MB)
Downloaded: 3 (4.77 MB), resumed: 1, checksum verified: 3
Already up to date: 0
Time: 0:03, average speed: 1.52 MB/s
Files not downloaded: 1
  a.txt: server returned HTTP 500
```

## Кратко по-русски

* `ydiskarc gui` — графический интерфейс: вставьте ссылку, выберите папку («Обзор…»),
  нажмите «Скачать».
* `ydiskarc sync ССЫЛКА -o ПАПКА` — скачивает раздачу или одну её подпапку (ссылка из
  браузера). Уже скачанные файлы пропускаются, недокачанные (`*.ydpart`) докачиваются,
  каждый файл сверяется по размеру и SHA-256 и получает исходную дату.
* Ошибка скачивания — это предупреждение, а не текст ошибки внутри файла; неудачные файлы и
  папки повторяются (`-r`), в конце печатается статистика и список нескачанных файлов.
* `-t N` — сколько файлов качать одновременно, `-c N` — сколько соединений на один файл
  (большие файлы качаются кусками параллельно; Яндекс ограничивает скорость одного
  соединения, так что это главный способ ускориться), `--flat` — все файлы в одну папку (для очень длинных
  путей), `--safe-names` — имена, допустимые в Windows, `-n` — только метаданные.

## Contributing

Contributions are welcome! Please follow these guidelines:

1. **Fork the repository** and create a feature branch
2. **Write tests** for new functionality
3. **Follow code style** - the project uses:
   - Black for formatting (line length: 100)
   - flake8 and ruff for linting
   - isort for import sorting
   - Type hints for better code clarity
   - All code must pass linting checks before submission
4. **Run tests** before submitting:
   ```bash
   pip install -r requirements-dev.txt
   pytest
   ```
5. **Run linting** to ensure code quality:
   ```bash
   black ydiskarc/
   isort ydiskarc/
   flake8 ydiskarc/
   ruff check ydiskarc/
   ```
6. **Install pre-commit hooks** (optional but recommended):
   ```bash
   pip install pre-commit
   pre-commit install
   ```
7. **Submit a pull request** with a clear description

### Development Setup

```bash
# Clone the repository
git clone https://github.com/ruarxive/ydiskarc.git
cd ydiskarc

# Install in development mode
pip install -e .

# Install development dependencies
pip install -r requirements-dev.txt

# Run tests
pytest

# Run linting
flake8 ydiskarc/
ruff check ydiskarc/
black --check ydiskarc/
isort --check-only ydiskarc/
```

## Metadata Documentation

For detailed information about the Yandex.Disk API metadata structure, see:
- [Examples Directory](examples/) - Contains example metadata file and documentation
- [Metadata Structure Documentation](examples/METADATA_STRUCTURE.md) - Complete reference of metadata fields

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for a detailed list of changes.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Links

- **Repository**: https://github.com/ruarxive/ydiskarc/
- **Issues**: https://github.com/ruarxive/ydiskarc/issues
- **Yandex.Disk API**: https://yandex.com/dev/disk/api/
