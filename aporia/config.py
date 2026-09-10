"""
Configuration and Environment management for APORIA.
"""

from __future__ import annotations
import os
from pathlib import Path
from typing import Any, Mapping


class Environment:
    """
    Environment configuration loader supporting explicit dict overrides,
    operating system environment variables, and optional .env file parsing.
    """

    def __init__(self, base_dir: str | Path = "", variables: Mapping[str, Any] | None = None) -> None:
        self.base_dir = Path(base_dir) if base_dir else Path.cwd()
        self._variables: dict[str, str] = {}

        # Optionally load .env file from base_dir if present
        env_file = self.base_dir / ".env"
        if env_file.is_file():
            self._load_env_file(env_file)

        # Merge process environment
        for k, v in os.environ.items():
            self._variables[k] = v

        # Merge explicit variable overrides
        if variables:
            for k, v in variables.items():
                self._variables[k] = str(v)

    def _load_env_file(self, path: Path) -> None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        self._variables[k] = v
        except Exception:
            pass

    def get_string(self, key: str, default: str = "") -> str:
        val = self._variables.get(key)
        if val is None:
            val = os.environ.get(key, default)
        return str(val)

    def getString(self, key: str, default: str = "") -> str:
        return self.get_string(key, default)

    def get_int(self, key: str, default: int = 0) -> int:
        val = self.get_string(key, "")
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def getInt(self, key: str, default: int = 0) -> int:
        return self.get_int(key, default)

    def get_bool(self, key: str, default: bool = False) -> bool:
        val = self.get_string(key, "").strip().lower()
        if val in ("1", "true", "yes", "on"):
            return True
        if val in ("0", "false", "no", "off"):
            return False
        return default

    def getBool(self, key: str, default: bool = False) -> bool:
        return self.get_bool(key, default)

    def app_environment(self) -> str:
        env = self.get_string("APP_ENV", "")
        if not env:
            env = self.get_string("APORIA_ENV", "production")
        return env.lower().strip()

    def appEnvironment(self) -> str:
        return self.app_environment()


Config = Environment
