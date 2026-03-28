# in working state

from robonet.logging_setup import setup_logging
import json
from pathlib import Path
from typing import Any

log = setup_logging()

_DEFAULT_SETTINGS: dict[str, Any] = {
    "jpeg_quality": 70,
    "play_audio": True,
}

_DEFAULT_ADVANCED_SETTINGS: dict[str, Any] = {
    "ai_res": [320, 240],
    "ai_fps": 60,
    "localhost_enabled": False,
    "our_port": 9999,
    "their_port": 9998,
    "adhoc_our_ip": "192.168.2.1",
    "adhoc_prev_connection" : "192.168.2.2",
    "wifi_prev_connection" : None,
    "adhoc_ssid"  : "robot_server",
    "adhoc_subnet": "192.168.2.0/24",
    "psk_file" : Path.home() / ".robobrain" / "psk.key",
    "server_psk_file" : Path.home() / ".robobrain" / "server_psk.key"
}

_ALL_SETTINGS = _DEFAULT_SETTINGS | _DEFAULT_ADVANCED_SETTINGS

_DEFAULT_PATH = Path.home() / ".robobrain" / "settings.json"

class Settings:
    def __init__(self, path: Path = _DEFAULT_PATH):
        self._path = path
        self._data: dict[str, Any] = dict(_DEFAULT_SETTINGS)
        self._load()

    def _load(self):
        if not self._path.exists():
            log.info("Settings file not found at %s — using defaults", self._path)
            return
        try:
            with open(self._path, encoding="utf-8") as fh:
                on_disk = json.load(fh)
            self._data.update(on_disk)
            log.debug("Settings loaded from %s", self._path)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not load settings (%s) — using defaults", exc)

    def save(self):
        """Persist current settings to disk (all keys, including file-only ones)."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
            log.debug("Settings saved to %s", self._path)
        except OSError as exc:
            log.error("Could not save settings: %s", exc)

    def __getitem__(self, key: str) -> Any:
        return self._data.get(key, _ALL_SETTINGS.get(key))

    def __setitem__(self, key: str, value: Any):
        if key not in _DEFAULT_SETTINGS.keys():
            raise KeyError(
                f"'{key}' is disabled. "
            )
        self._data[key] = value

    def sudo_set_item(self, key, value):
        self._data[key] = value

    def items(self) -> dict[str, Any]:
        """Return key:(value,enabled)"""
        return {k: (self._data.get(k, _ALL_SETTINGS.get(k)), k in _DEFAULT_SETTINGS.keys())
                for k in _ALL_SETTINGS.keys()}

_instance: Settings | None = None

def get() -> Settings:
    """Return the module-level Settings singleton, loading on first call."""
    global _instance
    if _instance is None:
        _instance = Settings()
    return _instance