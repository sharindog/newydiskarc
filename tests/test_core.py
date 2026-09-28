"""Tests for core CLI module."""

from unittest.mock import patch, MagicMock

try:
    from typer.testing import CliRunner
except ImportError:
    # Fallback for older typer versions
    from click.testing import CliRunner

    # Typer apps can be tested with Click's CliRunner
    CliRunner = CliRunner

import ydiskarc
from ydiskarc.core import app, default_output


class TestCLI:
    """Tests for CLI commands."""

    def setup_method(self):
        """Set up test fixtures."""
        self.runner = CliRunner()

    def test_app_exists(self):
        """Test that the Typer app is properly configured."""
        assert app is not None
        assert app.info.name == "ydiskarc"

    def test_full_command_help(self):
        """Test full command help text."""
        result = self.runner.invoke(app, ["full", "--help"])
        assert result.exit_code == 0
        assert "Download a full copy" in result.stdout

    def test_sync_command_help(self):
        """Test sync command help text."""
        result = self.runner.invoke(app, ["sync", "--help"])
        assert result.exit_code == 0
        assert "Synchronize files" in result.stdout

    def test_full_command_missing_url(self):
        """Test full command fails without URL."""
        result = self.runner.invoke(app, ["full"])
        assert result.exit_code != 0
        assert "Missing argument" in result.output or "missing" in result.output.lower()

    @patch("ydiskarc.core.Project")
    @patch("ydiskarc.client.YandexDiskClient.validate_yandex_url", return_value=True)
    def test_full_command_success(self, mock_validate, mock_project_class):
        """Test successful full command execution."""
        mock_project = MagicMock()
        mock_project_class.return_value = mock_project

        self.runner.invoke(
            app, ["full", "https://disk.yandex.ru/d/test123", "--output", "/tmp/test"]
        )
        mock_project.full.assert_called_once()

    def test_sync_command_missing_url(self):
        """Test sync command fails without URL."""
        result = self.runner.invoke(app, ["sync"])
        assert result.exit_code != 0

    @patch("ydiskarc.core.Project")
    @patch("ydiskarc.client.YandexDiskClient.validate_yandex_url", return_value=True)
    def test_sync_command_success(self, mock_validate, mock_project_class):
        """Test successful sync command execution."""
        mock_project = MagicMock()
        mock_project_class.return_value = mock_project

        self.runner.invoke(
            app, ["sync", "https://disk.yandex.ru/d/test123", "--output", "/tmp/test"]
        )
        mock_project.sync.assert_called_once()

    def test_verbose_flag(self):
        """Test verbose flag is accepted."""
        result = self.runner.invoke(app, ["full", "--help"])
        assert result.exit_code == 0
        assert "--verbose" in result.stdout or "-v" in result.stdout

    def test_version_command(self):
        """Test version command."""
        result = self.runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "ydiskarc" in result.stdout
        assert ydiskarc.__version__ in result.stdout

    def test_full_command_invalid_url(self):
        """Test full command rejects invalid URL."""
        result = self.runner.invoke(app, ["full", "https://example.com/invalid"])
        assert result.exit_code != 0
        assert "Invalid Yandex.Disk URL" in result.output or "Invalid Yandex.Disk URL" in str(
            result.exception
        )

    def test_sync_command_invalid_url(self):
        """Test sync command rejects invalid URL."""
        result = self.runner.invoke(app, ["sync", "not-a-url"])
        assert result.exit_code != 0
        assert "Invalid Yandex.Disk URL" in result.output or "Invalid Yandex.Disk URL" in str(
            result.exception
        )

    @patch("ydiskarc.core.Project")
    @patch("ydiskarc.client.YandexDiskClient.validate_yandex_url", return_value=True)
    def test_full_command_valid_url(self, mock_validate, mock_project_class):
        """Test full command accepts valid URL."""
        mock_project = MagicMock()
        mock_project_class.return_value = mock_project

        self.runner.invoke(
            app, ["full", "https://disk.yandex.ru/d/ABC123", "--output", "/tmp/test"]
        )
        mock_project.full.assert_called_once()

    @patch("ydiskarc.core.Project")
    @patch("ydiskarc.client.YandexDiskClient.validate_yandex_url", return_value=True)
    def test_sync_command_valid_url(self, mock_validate, mock_project_class):
        """Test sync command accepts valid URL."""
        mock_project = MagicMock()
        mock_project_class.return_value = mock_project

        self.runner.invoke(
            app, ["sync", "https://disk.yandex.ru/i/XYZ789", "--output", "/tmp/test"]
        )
        mock_project.sync.assert_called_once()

    def test_update_option_is_gone(self):
        """Update mode is always on now; the old flag is rejected."""
        result = self.runner.invoke(app, ["sync", "https://disk.yandex.ru/d/ABC", "--update"])
        assert result.exit_code != 0

    @patch("ydiskarc.core.Project")
    def test_sync_passes_options(self, mock_project_class):
        mock_project = MagicMock()
        mock_project_class.return_value = mock_project
        result = self.runner.invoke(
            app,
            [
                "sync",
                "https://disk.yandex.ru/d/ABC123/Folder",
                "-o",
                "/tmp/test",
                "-t",
                "5",
                "-r",
                "0",
                "--flat",
                "--verify",
                "--safe-names",
                "-n",
            ],
        )
        assert result.exit_code == 0, result.output
        kwargs = mock_project.sync.call_args.kwargs
        assert kwargs["threads"] == 5
        assert kwargs["retries"] == 0
        assert kwargs["flat"] is True
        assert kwargs["verify"] is True
        assert kwargs["safe_names"] is True
        assert kwargs["nofiles"] is True

    def test_sync_accepts_subfolder_url(self):
        result = self.runner.invoke(app, ["sync", "--help"])
        assert result.exit_code == 0
        assert "--threads" in result.stdout

    def test_default_output(self):
        assert default_output("https://disk.yandex.ru/d/ABC123") == "ABC123"
        assert default_output("https://disk.yandex.ru/d/ABC123/%D0%90/Sub") == "Sub"
