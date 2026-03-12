import logging
import threading
from pathlib import Path
from typing import Callable

import yaml
from pydantic import ValidationError
from watchdog.events import FileModifiedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from config.models import AppConfig

logger = logging.getLogger(__name__)


class ConfigLoader:
    """Loads, validates, saves, and hot-reloads settings.yaml.

    This is the single component responsible for reading/writing the config
    file. All other components receive ``AppConfig`` objects via callbacks or
    the ``config`` property.
    """

    def __init__(self, config_path: Path) -> None:
        self._path = config_path.resolve()
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[AppConfig], None]] = []
        self._config = self._load()
        self._observer = Observer()
        self._start_watching()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def config(self) -> AppConfig:
        with self._lock:
            return self._config

    def on_change(self, callback: Callable[[AppConfig], None]) -> None:
        """Register a callback invoked whenever the config is reloaded."""
        self._callbacks.append(callback)

    def save(self, config: AppConfig) -> None:
        """Persist *config* to disk, update in-memory copy, and notify callbacks.

        Callbacks are called directly here because the watchdog fires after the
        write but skips notification (``_on_file_changed`` sees the in-memory
        config already matches the file, so it returns early).
        """
        with self._lock:
            self._write(config)
            self._config = config

        for cb in self._callbacks:
            try:
                cb(config)
            except Exception:
                logger.exception("Error in config change callback")

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> AppConfig:
        with open(self._path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return AppConfig(**raw)

    def _write(self, config: AppConfig) -> None:
        data = config.model_dump()
        with open(self._path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    def _start_watching(self) -> None:
        handler = _ConfigFileHandler(self._path, self._on_file_changed)
        self._observer.schedule(handler, str(self._path.parent), recursive=False)
        self._observer.daemon = True
        self._observer.start()

    def _on_file_changed(self) -> None:
        try:
            new_config = self._load()
        except (yaml.YAMLError, ValidationError, OSError) as exc:
            logger.warning("Config reload failed: %s", exc)
            return

        with self._lock:
            if new_config == self._config:
                return  # no meaningful change (e.g. triggered by our own save)
            self._config = new_config

        logger.info("Config reloaded from %s", self._path)
        for cb in self._callbacks:
            try:
                cb(new_config)
            except Exception:
                logger.exception("Error in config change callback")


class _ConfigFileHandler(FileSystemEventHandler):
    def __init__(self, path: Path, callback: Callable[[], None]) -> None:
        self._path = path
        self._callback = callback

    def on_modified(self, event: FileModifiedEvent) -> None:
        if not event.is_directory and Path(event.src_path).resolve() == self._path:
            self._callback()
