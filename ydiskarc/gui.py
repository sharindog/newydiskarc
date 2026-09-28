"""Graphical interface (tkinter) for downloading public Yandex.Disk folders."""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from .client import YandexDiskClient, parse_public_url
from .config import config
from .i18n import set_language
from .progress import FileTask, Reporter, SyncStats, format_summary
from .utils import default_safe_names, format_duration, format_size, sanitize_filename

logger = logging.getLogger(__name__)

SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".ydiskarc-gui.json")

# Physical key codes of V, C, X, A: Tk ignores Ctrl+V & co. with a non-latin keyboard layout.
_EDIT_KEYCODES = {
    "win32": {86: "<<Paste>>", 67: "<<Copy>>", 88: "<<Cut>>", 65: "<<SelectAll>>"},
    "linux": {55: "<<Paste>>", 54: "<<Copy>>", 53: "<<Cut>>", 38: "<<SelectAll>>"},
}


class GuiReporter(Reporter):
    """Collects progress from worker threads; the Tk thread polls it."""

    def __init__(self) -> None:
        self.events: "queue.Queue[Tuple[Any, ...]]" = queue.Queue()
        self.lock = threading.Lock()
        self.active: Dict[int, Dict[str, Any]] = {}
        self.total_bytes = 0
        self.done_bytes = 0
        self.total_files = 0
        self.done_files = 0

    def scan_progress(self, dirs: int, files: int, size: int) -> None:
        self.events.put(("scan", dirs, files, size))

    def start(self, stats: SyncStats, pass_no: int, files: int, size: int) -> None:
        with self.lock:
            self.total_bytes = size
            self.done_bytes = 0
            self.total_files = files
            self.done_files = 0
        self.events.put(("start", pass_no, stats.total_files, stats.total_bytes, files, size))

    def add_total(self, size: int) -> None:
        with self.lock:
            self.total_bytes += size

    def file_start(self, task: FileTask, action: str = "download") -> None:
        with self.lock:
            self.active[id(task)] = {
                "name": task.display,
                "size": task.size,
                "done": 0,
                "action": action,
                "started": time.monotonic(),
            }

    def file_progress(self, task: FileTask, nbytes: int) -> None:
        with self.lock:
            info = self.active.get(id(task))
            if info is not None:
                info["done"] += nbytes
            self.done_bytes += nbytes

    def file_end(self, task: FileTask, status: str, error: Optional[str] = None) -> None:
        with self.lock:
            self.active.pop(id(task), None)
            if status in ("downloaded", "skipped", "failed"):
                self.done_files += 1

    def message(self, level: str, text: str) -> None:
        self.events.put(("message", level, text))

    def finish(self, stats: SyncStats) -> None:
        self.events.put(("finish", stats))

    def snapshot(self) -> Tuple[int, int, int, int, List[Dict[str, Any]]]:
        with self.lock:
            return (
                self.done_bytes,
                self.total_bytes,
                self.done_files,
                self.total_files,
                [dict(v, key=k) for k, v in self.active.items()],
            )


def load_settings() -> Dict[str, Any]:
    try:
        with open(SETTINGS_FILE, "r", encoding="utf8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data: Dict[str, Any]) -> None:
    try:
        with open(SETTINGS_FILE, "w", encoding="utf8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def default_download_dir() -> str:
    home = os.path.expanduser("~")
    for name in ("Downloads", "Загрузки"):
        path = os.path.join(home, name)
        if os.path.isdir(path):
            return path
    return home


def open_in_file_manager(path: str) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:  # pragma: no cover - depends on the desktop
        logger.warning("Cannot open %s: %s", path, e)


def resolve_target_dir(url: str, base_dir: str, make_subdir: bool, client=None) -> str:
    """Folder to save into: ``base_dir`` or ``base_dir/<name of the shared folder>``."""
    parsed = parse_public_url(url)
    if not make_subdir or parsed is None:
        return base_dir
    name = None
    is_file = False
    try:
        client = client or YandexDiskClient()
        data = client.get_resource_metadata(parsed.public_key, parsed.path or None, limit=1).json()
        name = data.get("name")
        is_file = data.get("type") == "file"
    except Exception as e:
        logger.debug("Cannot get resource name: %s", e)
    if is_file:
        return base_dir
    if not name:
        name = parsed.path.rstrip("/").rsplit("/", 1)[-1] if parsed.path else parsed.key
    return os.path.join(base_dir, sanitize_filename(name))


class App:
    POLL_MS = 200

    def __init__(self, root, url: Optional[str] = None) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = root
        self.settings = load_settings()
        self.worker: Optional[threading.Thread] = None
        self.cancel_event: Optional[threading.Event] = None
        self.reporter: Optional[GuiReporter] = None
        self.target_dir: Optional[str] = None
        self.speed_samples: Deque[Tuple[float, int]] = deque(maxlen=50)
        self.closing = False
        self.nofiles = False

        root.title("ydiskarc — загрузка с Яндекс.Диска")
        root.minsize(720, 560)
        root.geometry(self.settings.get("geometry") or "860x680")

        self.url_var = tk.StringVar(value=url or "")
        self.dir_var = tk.StringVar(value=self.settings.get("output") or default_download_dir())
        self.subdir_var = tk.BooleanVar(value=self.settings.get("subdir", True))
        self.threads_var = tk.IntVar(value=int(self.settings.get("threads", config.threads)))
        self.retries_var = tk.IntVar(value=int(self.settings.get("retries", config.retry_rounds)))
        self.nofiles_var = tk.BooleanVar(value=False)
        self.flat_var = tk.BooleanVar(value=self.settings.get("flat", False))
        self.verify_var = tk.BooleanVar(value=self.settings.get("verify", False))
        self.safe_var = tk.BooleanVar(value=self.settings.get("safe_names", default_safe_names()))
        self.status_var = tk.StringVar(value="Вставьте ссылку на публичную папку или файл.")
        self.progress_var = tk.StringVar(value="")

        self._build()
        self._install_edit_shortcuts()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(self.POLL_MS, self.poll)

    # ------------------------------------------------------------------ layout

    def _build(self) -> None:
        tk, ttk = self.tk, self.ttk
        pad = {"padx": 8, "pady": 4}
        main = ttk.Frame(self.root, padding=8)
        main.pack(fill="both", expand=True)
        main.columnconfigure(1, weight=1)

        ttk.Label(main, text="Ссылка:").grid(row=0, column=0, sticky="w", **pad)
        self.url_entry = ttk.Entry(main, textvariable=self.url_var)
        self.url_entry.grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(main, text="Вставить", command=self.paste_url).grid(row=0, column=2, **pad)

        ttk.Label(main, text="Сохранить в:").grid(row=1, column=0, sticky="w", **pad)
        self.dir_entry = ttk.Entry(main, textvariable=self.dir_var)
        self.dir_entry.grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(main, text="Обзор…", command=self.choose_dir).grid(row=1, column=2, **pad)
        ttk.Checkbutton(
            main, text="Создать подпапку с именем раздачи", variable=self.subdir_var
        ).grid(row=2, column=1, sticky="w", **pad)

        opts = ttk.LabelFrame(main, text="Параметры", padding=6)
        opts.grid(row=3, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Label(opts, text="Потоков:").grid(row=0, column=0, sticky="w", padx=4)
        ttk.Spinbox(opts, from_=1, to=16, width=4, textvariable=self.threads_var).grid(
            row=0, column=1, sticky="w", padx=4
        )
        ttk.Label(opts, text="Повторов для неудачных:").grid(row=0, column=2, sticky="w", padx=4)
        ttk.Spinbox(opts, from_=0, to=10, width=4, textvariable=self.retries_var).grid(
            row=0, column=3, sticky="w", padx=4
        )
        checks = [
            ("Только метаданные (_metadata.json), без файлов", self.nofiles_var),
            ("Все файлы в одну папку, без дерева (для очень длинных путей)", self.flat_var),
            ("Проверять SHA-256 уже скачанных файлов (медленно)", self.verify_var),
            ('Имена файлов, допустимые в Windows (замена " ? * : < > |)', self.safe_var),
        ]
        for i, (text, var) in enumerate(checks):
            ttk.Checkbutton(opts, text=text, variable=var).grid(
                row=1 + i, column=0, columnspan=4, sticky="w", padx=4
            )

        buttons = ttk.Frame(main)
        buttons.grid(row=4, column=0, columnspan=3, sticky="ew", **pad)
        self.start_btn = ttk.Button(buttons, text="Скачать", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(buttons, text="Остановить", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=8)
        ttk.Button(buttons, text="Открыть папку", command=self.open_dir).pack(side="left")

        ttk.Label(main, textvariable=self.status_var).grid(
            row=5, column=0, columnspan=3, sticky="w", **pad
        )
        self.progressbar = ttk.Progressbar(main, mode="determinate", maximum=1000)
        self.progressbar.grid(row=6, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Label(main, textvariable=self.progress_var).grid(
            row=7, column=0, columnspan=3, sticky="w", **pad
        )

        panes = ttk.PanedWindow(main, orient="vertical")
        panes.grid(row=8, column=0, columnspan=3, sticky="nsew", **pad)
        main.rowconfigure(8, weight=1)

        active = ttk.Frame(panes)
        self.tree = ttk.Treeview(
            active, columns=("size", "progress"), show="tree headings", height=5
        )
        self.tree.heading("#0", text="Сейчас скачивается")
        self.tree.heading("size", text="Размер")
        self.tree.heading("progress", text="Готово")
        self.tree.column("#0", stretch=True, width=480)
        self.tree.column("size", width=100, anchor="e", stretch=False)
        self.tree.column("progress", width=90, anchor="e", stretch=False)
        self.tree.pack(fill="both", expand=True)
        panes.add(active, weight=1)

        logframe = ttk.Frame(panes)
        self.log = tk.Text(logframe, height=10, wrap="word", state="disabled")
        scroll = ttk.Scrollbar(logframe, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.log.tag_configure("warning", foreground="#b36b00")
        self.log.tag_configure("error", foreground="#c00000")
        self.log.tag_configure("title", font=("TkDefaultFont", 10, "bold"))
        panes.add(logframe, weight=2)

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Вырезать", command=lambda: self._menu_event("<<Cut>>"))
        menu.add_command(label="Копировать", command=lambda: self._menu_event("<<Copy>>"))
        menu.add_command(label="Вставить", command=lambda: self._menu_event("<<Paste>>"))
        self.menu = menu
        self._menu_widget = None
        for widget in (self.url_entry, self.dir_entry, self.log):
            widget.bind("<Button-3>", self._show_menu)

    def _install_edit_shortcuts(self) -> None:
        codes = _EDIT_KEYCODES.get("win32" if sys.platform == "win32" else sys.platform, {})

        def handler(event):
            virtual = codes.get(event.keycode)
            if virtual and event.keysym.lower() not in ("v", "c", "x", "a"):
                event.widget.event_generate(virtual)
                return "break"
            return None

        if codes:
            self.root.bind_all("<Control-KeyPress>", handler, add="+")

    def _show_menu(self, event) -> None:
        self._menu_widget = event.widget
        self.menu.tk_popup(event.x_root, event.y_root)

    def _menu_event(self, name: str) -> None:
        if self._menu_widget is not None:
            self._menu_widget.focus_set()
            self._menu_widget.event_generate(name)

    # ------------------------------------------------------------------ actions

    def append_log(self, text: str, tag: Optional[str] = None) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", (tag,) if tag else ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def paste_url(self) -> None:
        try:
            text = self.root.clipboard_get().strip()
        except self.tk.TclError:
            return
        self.url_var.set(text)

    def choose_dir(self) -> None:
        from tkinter import filedialog

        initial = self.dir_var.get() if os.path.isdir(self.dir_var.get()) else None
        path = filedialog.askdirectory(
            parent=self.root, initialdir=initial, title="Куда сохранять файлы"
        )
        if path:
            self.dir_var.set(os.path.normpath(path))

    def open_dir(self) -> None:
        path = self.target_dir or self.dir_var.get()
        if path and os.path.isdir(path):
            open_in_file_manager(path)

    def _save_settings(self) -> None:
        self.settings.update(
            output=self.dir_var.get(),
            subdir=bool(self.subdir_var.get()),
            threads=self._int(self.threads_var, config.threads),
            retries=self._int(self.retries_var, config.retry_rounds),
            flat=bool(self.flat_var.get()),
            verify=bool(self.verify_var.get()),
            safe_names=bool(self.safe_var.get()),
            geometry=self.root.geometry(),
        )
        save_settings(self.settings)

    @staticmethod
    def _int(var, default: int) -> int:
        try:
            return int(var.get())
        except Exception:
            return default

    def start(self) -> None:
        from tkinter import messagebox

        url = self.url_var.get().strip()
        base_dir = self.dir_var.get().strip()
        if parse_public_url(url) is None:
            messagebox.showerror(
                "ydiskarc",
                "Это не похоже на публичную ссылку Яндекс.Диска.\n"
                "Пример: https://disk.yandex.ru/d/AbCdEf или ссылка на подпапку из браузера.",
                parent=self.root,
            )
            return
        if not base_dir:
            messagebox.showerror("ydiskarc", "Выберите папку для сохранения.", parent=self.root)
            return
        try:
            os.makedirs(base_dir, exist_ok=True)
        except OSError as e:
            messagebox.showerror("ydiskarc", f"Не удалось создать папку:\n{e}", parent=self.root)
            return
        self._save_settings()

        self.cancel_event = threading.Event()
        self.reporter = GuiReporter()
        self.speed_samples.clear()
        self.progressbar.configure(mode="indeterminate")
        self.progressbar.start(15)
        self.progress_var.set("")
        self.status_var.set("Получение списка файлов…")
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.append_log(f"— {time.strftime('%H:%M:%S')} {url}", "title")

        options: Dict[str, Any] = dict(
            url=url,
            base_dir=base_dir,
            subdir=bool(self.subdir_var.get()),
            nofiles=bool(self.nofiles_var.get()),
            threads=max(1, self._int(self.threads_var, config.threads)),
            retries=max(0, self._int(self.retries_var, config.retry_rounds)),
            flat=bool(self.flat_var.get()),
            verify=bool(self.verify_var.get()),
            safe_names=bool(self.safe_var.get()),
        )
        self.nofiles = options["nofiles"]
        self.worker = threading.Thread(target=self._work, args=(options,), daemon=True)
        self.worker.start()

    def _work(self, options: Dict[str, Any]) -> None:
        from .cmds.processor import Project

        reporter = self.reporter
        assert reporter is not None and self.cancel_event is not None
        try:
            target = resolve_target_dir(
                options["url"],
                options["base_dir"],
                options["subdir"],
                YandexDiskClient(cancel_event=self.cancel_event),
            )
            reporter.events.put(("target", target))
            Project().sync(
                options["url"],
                target,
                nofiles=options["nofiles"],
                threads=options["threads"],
                flat=options["flat"],
                safe_names=options["safe_names"],
                verify=options["verify"],
                retries=options["retries"],
                reporter=reporter,
                cancel_event=self.cancel_event,
            )
        except Exception as e:  # the engine reports its own errors; this is a last resort
            logger.debug("GUI worker failed", exc_info=True)
            stats = SyncStats(error=str(e) or e.__class__.__name__)
            reporter.finish(stats)

    def stop(self) -> None:
        if self.cancel_event is not None:
            self.cancel_event.set()
            self.status_var.set(
                "Остановка… (недокачанные файлы будут докачаны при следующем запуске)"
            )
            self.stop_btn.configure(state="disabled")

    def on_close(self) -> None:
        from tkinter import messagebox

        if self.worker is not None and self.worker.is_alive():
            if not messagebox.askyesno(
                "ydiskarc", "Загрузка ещё идёт. Остановить и выйти?", parent=self.root
            ):
                return
            self.closing = True
            self.stop()
            return
        self._save_settings()
        self.root.destroy()

    # ------------------------------------------------------------------ polling

    def poll(self) -> None:
        try:
            self._drain_events()
            self._refresh_progress()
        except Exception:  # never let the poll loop die
            logger.debug("GUI poll failed", exc_info=True)
        if self.closing and (self.worker is None or not self.worker.is_alive()):
            self._save_settings()
            self.root.destroy()
            return
        self.root.after(self.POLL_MS, self.poll)

    def _drain_events(self) -> None:
        reporter = self.reporter
        if reporter is None:
            return
        while True:
            try:
                event = reporter.events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "target":
                self.target_dir = event[1]
                self.append_log(f"Папка: {event[1]}")
            elif kind == "scan":
                _, dirs, files, size = event
                self.status_var.set(
                    f"Сканирование: папок {dirs}, файлов {files} ({format_size(size)})…"
                )
            elif kind == "start":
                _, pass_no, total_files, total_bytes, files, size = event
                self.progressbar.stop()
                self.progressbar.configure(mode="determinate", value=0)
                self.speed_samples.clear()
                if pass_no == 0:
                    self.append_log(
                        f"Всего файлов: {total_files} ({format_size(total_bytes)}); "
                        f"нужно скачать: {files} ({format_size(size)})"
                    )
                    self.status_var.set("Скачивание…" if not self.nofiles else "Готово.")
                else:
                    self.status_var.set(f"Повторная попытка {pass_no}…")
            elif kind == "message":
                _, level, text = event
                self.append_log(text, level if level in ("warning", "error") else None)
            elif kind == "finish":
                self._finished(event[1])

    def _refresh_progress(self) -> None:
        reporter = self.reporter
        if reporter is None or str(self.progressbar.cget("mode")) != "determinate":
            return
        done, total, done_files, total_files, active = reporter.snapshot()
        now = time.monotonic()
        self.speed_samples.append((now, done))
        while len(self.speed_samples) > 2 and now - self.speed_samples[0][0] > 8:
            self.speed_samples.popleft()
        speed = 0.0
        if len(self.speed_samples) >= 2:
            (t0, b0), (t1, b1) = self.speed_samples[0], self.speed_samples[-1]
            if t1 > t0:
                speed = max(b1 - b0, 0) / (t1 - t0)
        fraction = done / total if total else (done_files / total_files if total_files else 0)
        self.progressbar.configure(value=max(0, min(1000, int(fraction * 1000))))
        text = f"{format_size(done)} из {format_size(total)} · файлов {done_files}/{total_files}"
        if speed > 0:
            text += f" · {format_size(speed)}/с"
            if total > done:
                text += f" · осталось ≈ {format_duration((total - done) / speed)}"
        self.progress_var.set(text)

        existing = set(self.tree.get_children())
        current = set()
        for info in active:
            iid = str(info["key"])
            current.add(iid)
            if info["action"] == "verify":
                size_text, progress_text = format_size(info["size"]), "проверка"
            else:
                size_text = format_size(info["size"])
                progress_text = (
                    f"{min(100, info['done'] * 100 // info['size'])}%"
                    if info["size"]
                    else format_size(info["done"])
                )
            if iid in existing:
                self.tree.item(iid, values=(size_text, progress_text))
            else:
                self.tree.insert(
                    "", "end", iid=iid, text=info["name"], values=(size_text, progress_text)
                )
        for iid in existing - current:
            self.tree.delete(iid)

    def _finished(self, stats: SyncStats) -> None:
        self._refresh_progress()
        self.progressbar.stop()
        self.progressbar.configure(mode="determinate")
        if stats.ok:
            self.progressbar.configure(value=1000)
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for level, line in format_summary(stats, self.nofiles):
            self.append_log(line, level if level in ("warning", "error") else None)
        if stats.cancelled:
            self.status_var.set("Остановлено.")
        elif stats.error:
            self.status_var.set("Ошибка — подробности в журнале.")
        elif stats.failed_files or stats.failed_dirs:
            self.status_var.set(
                f"Завершено с ошибками: не скачано файлов {len(stats.failed_files)}, "
                f"папок {len(stats.failed_dirs)}. Можно запустить ещё раз."
            )
        else:
            self.status_var.set("Готово.")
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.worker = None


def run_gui(url: Optional[str] = None) -> None:
    try:
        import tkinter as tk
    except ImportError:  # pragma: no cover - depends on the Python build
        sys.stderr.write(
            "tkinter is not available. Install it (e.g. `apt install python3-tk`) "
            "or use the command line interface.\n"
        )
        raise SystemExit(1)
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
        except Exception:
            pass
    set_language("ru")
    root = tk.Tk()
    App(root, url)
    root.mainloop()


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else None
    run_gui(url)


if __name__ == "__main__":
    main()
