"""Tests for platform integration: sleep prevention, app launcher, GUI helpers."""

import importlib.util
import os
import sys

import pytest

from ydiskarc import power
from ydiskarc.config import Config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_launcher():
    path = os.path.join(ROOT, "packaging", "ydiskarc_launcher.py")
    spec = importlib.util.spec_from_file_location("ydiskarc_launcher", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeProcess:
    def __init__(self, args, **kwargs):
        self.args = args
        self.terminated = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def test_keep_awake_uses_caffeinate_on_macos(monkeypatch):
    started = []

    def fake_popen(args, **kwargs):
        started.append(FakeProcess(args))
        return started[-1]

    monkeypatch.setattr(power.sys, "platform", "darwin")
    monkeypatch.setattr(power.subprocess, "Popen", fake_popen)
    with power.KeepAwake():
        assert started[0].args[:3] == ["caffeinate", "-i", "-s"]
        assert started[0].args[-1] == str(os.getpid())
    assert started[0].terminated


def test_keep_awake_ignores_failures(monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("no caffeinate")

    monkeypatch.setattr(power.sys, "platform", "darwin")
    monkeypatch.setattr(power.subprocess, "Popen", broken)
    with power.KeepAwake():
        pass


def test_api_url_can_be_overridden(monkeypatch):
    monkeypatch.setenv("YDISKARC_API_BASE_URL", "http://127.0.0.1:1/v1/disk")
    assert Config().api_resources_url == "http://127.0.0.1:1/v1/disk/public/resources"


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["app"], "gui"),
        (["app", "-psn_0_12345"], "gui"),
        (["app", "--smoke-test"], "gui"),
        (["app", "sync", "https://disk.yandex.ru/d/X"], "cli"),
        (["app", "-psn_0_1", "version"], "cli"),
    ],
)
def test_launcher_dispatch(monkeypatch, argv, expected):
    launcher = load_launcher()
    called = []
    monkeypatch.setattr(sys, "argv", list(argv))
    monkeypatch.setattr("ydiskarc.__main__.main", lambda: called.append(("cli", sys.argv[1:])))
    gui = pytest.importorskip("ydiskarc.gui")
    monkeypatch.setattr(gui, "main", lambda: called.append(("gui", None)))
    launcher.main()
    assert called[0][0] == expected
    if expected == "cli":
        assert not any(a.startswith("-psn_") for a in called[0][1])


def test_edit_shortcuts_with_russian_layout():
    gui = pytest.importorskip("ydiskarc.gui")
    assert gui.edit_shortcut_event("Cyrillic_em", 0, "darwin") == "<<Paste>>"
    assert gui.edit_shortcut_event("Cyrillic_es", 0, "darwin") == "<<Copy>>"
    assert gui.edit_shortcut_event("??", 86, "win32") == "<<Paste>>"
    assert gui.edit_shortcut_event("v", 86, "win32") is None  # handled by Tk
    assert gui.edit_shortcut_event("b", 56, "linux") is None
