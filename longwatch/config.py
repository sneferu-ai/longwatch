"""Configuration — environment variables only (§12 of the specification).

There are no config files and there is deliberately no demo-seed variable:
demo data exists only behind the explicit operator action in §16.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DATA_DIR = "/data"


def _env(name: str, default=None):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def env_str(name: str, default: str) -> str:
    return _env(name, default)


def env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


def env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    """Every knob from §12, read once at process start."""

    port: int = 8000
    host: str = "0.0.0.0"
    database_url: str = ""
    data_dir: str = DEFAULT_DATA_DIR
    inbox_dir: str | None = None
    inbox_poll_s: float = 0.0
    reviewer_name: str = "operator"
    offline: bool = True
    auth_token: str | None = None
    detect_fps: float = 2.0
    static_fps: float = 0.5
    static_motion: float = 0.02
    conf_min: float = 0.25
    nms_iou: float = 0.45
    scene_t0: float = 0.30
    coverage_s: float = 300.0
    window_min_s: float = 10.0
    window_max_s: float = 180.0
    proxy_long_edge: int = 640
    model_path: str = "/opt/longwatch/models/yolov8n.onnx"
    model_sha256: str | None = None
    class_map_path: str = "/opt/longwatch/models/coco80.json"
    blur_faces: bool = False
    face_model_path: str = "/opt/longwatch/models/yunet.onnx"
    face_model_sha256: str | None = None
    sign_key_path: str | None = None
    upload_limit_mb: int = 4096
    upload_concurrency: int = 2
    job_lease_s: int = 120
    ledger_verify_s: int = 3600
    ort_threads: int = 1
    export_top_n: int = 200
    export_states: tuple[str, ...] = ("accepted", "flagged")
    job_poll_ms: int = 2000
    queue_limit: int = 50
    queue_limit_max: int = 200
    ffmpeg_hang_base_s: float = 60.0
    ffmpeg_hang_factor: float = 4.0
    store_frames: bool = False
    extra: dict = field(default_factory=dict)

    # ---------------------------------------------------------------- derived
    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    def subdir(self, name: str) -> Path:
        return self.data_path / name

    @property
    def key_path(self) -> Path:
        if self.sign_key_path:
            return Path(self.sign_key_path)
        return self.subdir("keys") / "ed25519.pem"

    def ensure_dirs(self) -> None:
        for name in ("inbox", "originals", "proxies", "excerpts", "exports",
                     "keys", "tmp", "fixtures"):
            (self.data_path / name).mkdir(parents=True, exist_ok=True)

    @property
    def detect_fps_clamped(self) -> float:
        return min(5.0, max(0.5, float(self.detect_fps)))

    # ------------------------------------------------------------------ build
    @classmethod
    def from_env(cls) -> "Config":
        data_dir = env_str("LONGWATCH_DATA_DIR", DEFAULT_DATA_DIR)
        # SOD supplies the writable directory as SOD_DATA_DIR and the container
        # port as PORT; both are honoured when the LONGWATCH_* names are absent.
        if _env("LONGWATCH_DATA_DIR") is None and _env("SOD_DATA_DIR"):
            data_dir = str(_env("SOD_DATA_DIR"))
        token = _env("LONGWATCH_AUTH_TOKEN")
        states_raw = env_str("LONGWATCH_EXPORT_STATES", "accepted,flagged")
        states = tuple(s.strip() for s in states_raw.split(",") if s.strip())
        cfg = cls(
            port=env_int("PORT", env_int("LONGWATCH_PORT", 8000)),
            host=env_str("HOST", env_str("LONGWATCH_HOST", "0.0.0.0")),
            database_url=env_str("DATABASE_URL", ""),
            data_dir=data_dir,
            inbox_dir=_env("LONGWATCH_INBOX_DIR"),
            inbox_poll_s=env_float("LONGWATCH_INBOX_POLL_S", 0.0),
            reviewer_name=env_str("LONGWATCH_REVIEWER_NAME", "operator"),
            offline=env_bool("LONGWATCH_OFFLINE", True),
            auth_token=token,
            detect_fps=env_float("LONGWATCH_DETECT_FPS", 2.0),
            static_fps=env_float("LONGWATCH_STATIC_FPS", 0.5),
            static_motion=env_float("LONGWATCH_STATIC_MOTION", 0.02),
            conf_min=env_float("LONGWATCH_CONF_MIN", 0.25),
            nms_iou=env_float("LONGWATCH_NMS_IOU", 0.45),
            scene_t0=env_float("LONGWATCH_SCENE_T0", 0.30),
            coverage_s=env_float("LONGWATCH_COVERAGE_S", 300.0),
            window_min_s=env_float("LONGWATCH_WINDOW_MIN_S", 10.0),
            window_max_s=env_float("LONGWATCH_WINDOW_MAX_S", 180.0),
            proxy_long_edge=env_int("LONGWATCH_PROXY_LONG_EDGE", 640),
            model_path=env_str("LONGWATCH_MODEL_PATH", "/opt/longwatch/models/yolov8n.onnx"),
            model_sha256=_env("LONGWATCH_MODEL_SHA256"),
            class_map_path=env_str("LONGWATCH_CLASS_MAP", "/opt/longwatch/models/coco80.json"),
            blur_faces=env_bool("LONGWATCH_BLUR_FACES", False),
            face_model_path=env_str("LONGWATCH_FACE_MODEL_PATH", "/opt/longwatch/models/yunet.onnx"),
            face_model_sha256=_env("LONGWATCH_FACE_MODEL_SHA256"),
            sign_key_path=_env("LONGWATCH_SIGN_KEY_PATH"),
            upload_limit_mb=env_int("LONGWATCH_UPLOAD_LIMIT_MB", 4096),
            upload_concurrency=env_int("LONGWATCH_UPLOAD_CONCURRENCY", 2),
            job_lease_s=env_int("LONGWATCH_JOB_LEASE_S", 120),
            ledger_verify_s=env_int("LONGWATCH_LEDGER_VERIFY_S", 3600),
            ort_threads=env_int("LONGWATCH_ORT_THREADS", 1),
            export_top_n=env_int("LONGWATCH_EXPORT_TOP_N", 200),
            export_states=states or ("accepted", "flagged"),
            job_poll_ms=env_int("LONGWATCH_JOB_POLL_MS", 2000),
            queue_limit=env_int("LONGWATCH_QUEUE_LIMIT", 50),
            ffmpeg_hang_base_s=env_float("LONGWATCH_FFMPEG_HANG_BASE_S", 60.0),
            ffmpeg_hang_factor=env_float("LONGWATCH_FFMPEG_HANG_FACTOR", 4.0),
            store_frames=env_bool("LONGWATCH_STORE_FRAMES", False),
        )
        return cfg


_cached: Config | None = None


def get_config(refresh: bool = False) -> Config:
    global _cached
    if _cached is None or refresh:
        _cached = Config.from_env()
    return _cached
