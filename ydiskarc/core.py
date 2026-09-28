#!/usr/bin/env python
# -*- coding: utf8 -*-
import logging
from typing import Optional

import typer

import ydiskarc

from .cmds.processor import Project
from .client import YandexDiskClient, parse_public_url
from .config import config
from .utils import sanitize_filename

# Create Typer app
app = typer.Typer(
    name="ydiskarc",
    help="A command-line tool to backup public resources from Yandex.Disk",
    add_completion=False,
)

INVALID_URL_HELP = (
    "URL must be in format: https://disk.yandex.ru/d/... or https://disk.yandex.ru/i/...\n"
    "A link to a subfolder copied from the browser (https://disk.yandex.ru/d/KEY/Folder) "
    "is accepted too."
)


def _console():
    from rich.console import Console

    return Console(stderr=True)


def setup_logging(verbose: bool = False, console=None) -> None:
    """Setup logging configuration.

    Args:
        verbose: If True, show debug messages (including HTTP requests); otherwise only
            warnings and errors are logged.
        console: rich console shared with the progress bars, so that log lines do not
            break them.
    """
    from rich.logging import RichHandler

    handler = RichHandler(
        console=console or _console(), show_path=verbose, rich_tracebacks=verbose, markup=False
    )
    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.WARNING)
    # urllib3 reports every retry as a warning; the downloader reports failures itself.
    logging.getLogger("urllib3").setLevel(logging.DEBUG if verbose else logging.ERROR)
    logging.disable(logging.NOTSET)


def _check_url(url: str) -> None:
    if not YandexDiskClient.validate_yandex_url(url):
        typer.echo(f"Invalid Yandex.Disk URL: {url}\n{INVALID_URL_HELP}", err=True)
        raise typer.Exit(1)


def default_output(url: str) -> str:
    """Default output folder: the resource key, or the name of the linked subfolder."""
    parsed = parse_public_url(url)
    if parsed is None:
        return url.rstrip("/").rsplit("/", 1)[-1]
    if parsed.path.strip("/"):
        return sanitize_filename(parsed.path.rstrip("/").rsplit("/", 1)[-1])
    return sanitize_filename(parsed.key)


def _exit_code(stats) -> int:
    from .progress import SyncStats

    if not isinstance(stats, SyncStats) or stats.ok:
        return 0
    return 130 if stats.cancelled else 1


def _make_reporter(console, nofiles: bool = False):
    from .progress import RichReporter

    return RichReporter(console=console, nofiles=nofiles)


@app.command("full")
def full(
    url: str = typer.Argument(..., help="URL of the Yandex.Disk public resource to download"),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="Output directory where files will be saved"
    ),
    filename: Optional[str] = typer.Option(
        None,
        "--filename",
        "-f",
        help="Output filename (default: original name of a file, dump.zip for a folder)",
    ),
    metadata: bool = typer.Option(
        False, "--metadata", "-m", help="Extract and save metadata as _metadata.json file"
    ),
    safe_names: Optional[bool] = typer.Option(
        None,
        "--safe-names/--native-names",
        help="Replace characters not allowed on Windows/FAT/NTFS (default: on for Windows)",
    ),
    connections: int = typer.Option(
        config.connections_per_file,
        "--connections",
        "-c",
        min=1,
        max=config.max_connections_per_file,
        help="Connections for a big file (it is downloaded in segments in parallel)",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable verbose output with detailed logging information"
    ),
):
    """Download a full copy of public resource files from Yandex.Disk.

    This command downloads a single file or directory from a public Yandex.Disk resource.
    Single files are downloaded with their original format, while directories are downloaded
    as ZIP files containing all files inside.
    """
    console = _console()
    setup_logging(verbose, console)
    _check_url(url)
    try:
        acmd = Project()
        with _make_reporter(console) as reporter:
            stats = acmd.full(
                url,
                output,
                filename,
                metadata,
                verbose,
                reporter=reporter,
                safe_names=safe_names,
                connections=connections,
            )
    except Exception as e:
        logging.debug("Error during full download", exc_info=True)
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    code = _exit_code(stats)
    if code:
        raise typer.Exit(code)


@app.command("sync")
def sync(
    url: str = typer.Argument(
        ...,
        help="URL of the public Yandex.Disk folder (or of a subfolder, copied from the browser)",
    ),
    output: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        help=(
            "Output path where synchronized files will be stored "
            "(defaults to resource ID if not specified)"
        ),
    ),
    nofiles: bool = typer.Option(
        False,
        "--nofiles",
        "-n",
        help="Metadata-only mode: save _metadata.json for every folder, download no files",
    ),
    threads: int = typer.Option(
        config.threads,
        "--threads",
        "-t",
        min=1,
        max=config.max_threads,
        help="Number of files downloaded in parallel",
    ),
    connections: int = typer.Option(
        config.connections_per_file,
        "--connections",
        "-c",
        min=1,
        max=config.max_connections_per_file,
        help="Connections per big file (it is downloaded in segments in parallel)",
    ),
    retries: int = typer.Option(
        config.retry_rounds,
        "--retries",
        "-r",
        min=0,
        help="Extra passes over files and folders that failed",
    ),
    flat: bool = typer.Option(
        False,
        "--flat",
        help="Save all files directly into the output folder, without the folder tree "
        "(for very long paths)",
    ),
    safe_names: Optional[bool] = typer.Option(
        None,
        "--safe-names/--native-names",
        help="Replace characters not allowed on Windows/FAT/NTFS (default: on for Windows)",
    ),
    verify: bool = typer.Option(
        False,
        "--verify",
        help="Check SHA-256 of files that already exist locally (slow for big backups)",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable verbose output with detailed logging information"
    ),
):
    """Synchronize files and metadata from a public Yandex.Disk resource.

    Files that already exist locally and are up to date are skipped, new and changed files
    are downloaded, checked against the size and SHA-256 reported by Yandex.Disk and get
    their original modification time. Interrupted downloads are resumed.
    """
    console = _console()
    setup_logging(verbose, console)
    _check_url(url)
    if output is None:
        output = default_output(url)
    try:
        acmd = Project()
        with _make_reporter(console, nofiles) as reporter:
            stats = acmd.sync(
                url,
                output,
                nofiles=nofiles,
                verbose=verbose,
                threads=threads,
                connections=connections,
                flat=flat,
                safe_names=safe_names,
                verify=verify,
                retries=retries,
                reporter=reporter,
            )
    except Exception as e:
        logging.debug("Error during sync", exc_info=True)
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    code = _exit_code(stats)
    if code:
        raise typer.Exit(code)


@app.command("gui")
def gui(
    url: Optional[str] = typer.Argument(None, help="URL to put into the window"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Log debug messages to console"),
):
    """Open the graphical interface."""
    setup_logging(verbose)
    from .gui import run_gui

    run_gui(url)


@app.command("version")
def version():
    """Show version information."""
    typer.echo(f"ydiskarc {ydiskarc.__version__}")


def cli() -> None:
    """Main CLI entry point that shows help when no command is provided."""
    # Typer automatically shows help when no command is provided
    app()


if __name__ == "__main__":
    cli()
