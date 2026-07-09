from robonet.logging_setup import setup_logging
import json
import os
from pathlib import Path
from typing import Any

log = setup_logging()

_SETTINGS: dict[str, Any] = {
    "jpeg_quality": 70,
    "cam_res": [640, 480],
    "cam_fps": 30,
    "endpoints": ["robot", "desktop"],
    "our_port": 9998,
    "their_port": 9999,
    "adhoc_their_ip": "192.168.2.1",
    "adhoc_ssid": "robot_server",
    "adhoc_subnet": "192.168.2.0/24",
    # Matches robonet/brain/settings.py's wired_our_ip/wired_subnet -- see
    # examples/setup_eth_client.py. Both sides are hardcoded to agree on
    # this subnet out of the box since they're independent settings files,
    # possibly on different machines, with no automatic way to sync a
    # chosen value between them.
    "wired_endpoint_ip": "169.254.90.2",
    "wired_subnet": "169.254.90.0/24",
    # Off by default: attempting this unconditionally on every endpoint
    # startup would reconfigure the first ethernet interface with a cable
    # plugged in via nmcli, which could just as easily be someone's normal
    # wired internet connection, not one intended for robonet pairing.
    # Turn this on for endpoints that are actually meant to be reached over
    # a direct wired link -- see RobotRadio.__init__.
    "auto_wired_setup": False,
    "psk_file": Path.home() / ".robotar" / "psk.key",
    "server_psk_file": Path.home() / ".robotar" / "server_psk.key"
}

_DEFAULT_PATH = Path.home() / ".robotar" / "settings.json"

class Settings:
    def __init__(self, path: Path = _DEFAULT_PATH):
        self._path = path
        self._data: dict[str, Any] = dict(_SETTINGS)
        self._load()

    def _load(self):
        if not self._path.exists():
            log.info("Settings file not found at %s -- using defaults", self._path)
            return
        try:
            with open(self._path, encoding="utf-8") as fh:
                on_disk = json.load(fh)
            self._data.update(on_disk)
            log.debug("Settings loaded from %s", self._path)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not load settings (%s) -- using defaults", exc)

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
        return self._data.get(key, _SETTINGS[key])

    def __setitem__(self, key: str, value: Any):
        if key not in _SETTINGS.keys():
            raise KeyError(
                f"'{key}' is not in settings. "
            )
        self._data[key] = value

    def items(self) -> dict[str, Any]:
        """Return key:(value,enabled)"""
        return {k: (self._data.get(k, _SETTINGS.get(k)), k in _SETTINGS.keys())
                for k in _SETTINGS.keys()}

_instance: Settings | None = None

def get() -> Settings:
    """Return the module-level Settings singleton, loading on first call."""
    global _instance
    if _instance is None:
        _instance = Settings()
    return _instance