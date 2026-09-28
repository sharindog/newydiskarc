"""Minimal message translation (the console speaks English, the GUI speaks Russian)."""

from typing import Dict

_language = "en"

MESSAGES: Dict[str, Dict[str, str]] = {
    "en": {
        "failed_folder": "Failed to read folder {path}: {reason}",
        "failed_file": "Failed to download {path}: {reason}",
        "retry_pass": "Retry {n}/{total}: {files} file(s), {dirs} folder(s) in {pause} s",
        "stopped": "Stopped by user. Run again to resume.",
        "error": "Error: {error}",
        "totals": "Folders: {dirs}, files: {files} ({size})",
        "metadata_written": "Metadata files written: {count}",
        "downloaded": "Downloaded: {count} ({size})",
        "resumed": ", resumed: {count}",
        "verified": ", checksum verified: {count}",
        "up_to_date": "Already up to date: {count}",
        "time": "Time: {time}, average speed: {speed}/s",
        "failed_folders": "Folders that could not be read: {count}",
        "failed_files": "Files not downloaded: {count}",
        "done": "Done, everything is up to date.",
        "done_meta": "Done.",
    },
    "ru": {
        "failed_folder": "Не удалось прочитать папку {path}: {reason}",
        "failed_file": "Не удалось скачать {path}: {reason}",
        "retry_pass": "Повтор {n}/{total}: файлов {files}, папок {dirs}, через {pause} с",
        "stopped": "Остановлено пользователем. Запустите снова, чтобы докачать.",
        "error": "Ошибка: {error}",
        "totals": "Папок: {dirs}, файлов: {files} ({size})",
        "metadata_written": "Записано файлов метаданных: {count}",
        "downloaded": "Скачано: {count} ({size})",
        "resumed": ", докачано после обрыва: {count}",
        "verified": ", проверено по контрольной сумме: {count}",
        "up_to_date": "Уже были скачаны: {count}",
        "time": "Время: {time}, средняя скорость: {speed}/с",
        "failed_folders": "Не удалось прочитать папок: {count}",
        "failed_files": "Не скачано файлов: {count}",
        "done": "Готово, всё скачано.",
        "done_meta": "Готово.",
    },
}


def set_language(language: str) -> None:
    global _language
    _language = language if language in MESSAGES else "en"


def tr(key: str, **kwargs: object) -> str:
    template = MESSAGES.get(_language, MESSAGES["en"]).get(key) or MESSAGES["en"][key]
    return template.format(**kwargs)
