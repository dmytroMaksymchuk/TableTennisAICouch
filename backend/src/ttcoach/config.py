"""Settings (DESIGN §6.2). Paths are anchored to the repo root, never to the cwd."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

# backend/src/ttcoach/config.py -> parents[3] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = REPO_ROOT / "backend"
LOCAL_TOML = BACKEND_DIR / "ttcoach.local.toml"


def _which(name: str) -> str | None:
    return shutil.which(name)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TTCOACH_", toml_file=LOCAL_TOML, extra="ignore")

    data_dir: Path = REPO_ROOT / "data"
    rules_path: Path = BACKEND_DIR / "rules" / "default.yaml"
    pose_estimator: str = "sports2d"
    estimator_options: dict[str, dict[str, Any]] = Field(default_factory=dict)
    ffmpeg_path: str | None = Field(default_factory=lambda: _which("ffmpeg"))
    ffprobe_path: str | None = Field(default_factory=lambda: _which("ffprobe"))
    max_video_height: int = 720
    max_fps: int = 60
    max_duration_s: float = 300.0
    max_upload_bytes: int = 2 * 1024**3
    smoothing_cutoff_hz_max: float = 12.0

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, env_settings, TomlConfigSettingsSource(settings_cls))

    def model_post_init(self, __context: Any) -> None:
        # Relative paths in env/TOML are interpreted against the repo root, not the cwd.
        if not self.data_dir.is_absolute():
            self.data_dir = (REPO_ROOT / self.data_dir).resolve()
        if not self.rules_path.is_absolute():
            self.rules_path = (REPO_ROOT / self.rules_path).resolve()

    def options_for(self, estimator: str) -> dict[str, Any]:
        return dict(self.estimator_options.get(estimator, {}))
