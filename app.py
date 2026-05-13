from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, render_template, request, send_from_directory, url_for


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CONFIG_PATH = DATA_DIR / "config.json"
CAPTURES_DIR = DATA_DIR / "captures"
PREVIEWS_DIR = DATA_DIR / "previews"

CAMERA_IDS = [0, 1, 2, 3]
CAPTURE_RESOLUTIONS = {
    "1920x1080": (1920, 1080),
    "1280x720": (1280, 720),
    "960x720": (960, 720),
    "640x480": (640, 480),
}
DEFAULT_CAPTURE_RESOLUTION = "1920x1080"
PREVIEW_CARD_WIDTH = 640
PREVIEW_CARD_HEIGHT = 480
PREVIEW_MODAL_WIDTH = 960
PREVIEW_MODAL_HEIGHT = 720


@dataclass
class CameraConfig:
    prefix: str = "camera"
    interval_minutes: int = 10
    enabled: bool = False
    sequence: int = 0
    capture_resolution: str = DEFAULT_CAPTURE_RESOLUTION


def ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    CAPTURES_DIR.mkdir(exist_ok=True)
    PREVIEWS_DIR.mkdir(exist_ok=True)


def default_config() -> dict:
    return {
        "config_revision": 0,
        "cameras": {
            str(camera_id): asdict(CameraConfig(prefix=f"cam{camera_id}")) for camera_id in CAMERA_IDS
        }
    }


def load_config() -> dict:
    ensure_dirs()
    if not CONFIG_PATH.exists():
        config = default_config()
        save_config(config)
        return config

    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (json.JSONDecodeError, OSError):
        raw = default_config()
        save_config(raw)
        return raw

    changed = False
    if "config_revision" not in raw:
        raw["config_revision"] = 0
        changed = True
    raw.setdefault("cameras", {})
    for camera_id in CAMERA_IDS:
        key = str(camera_id)
        if key not in raw["cameras"]:
            raw["cameras"][key] = asdict(CameraConfig(prefix=f"cam{camera_id}"))
            changed = True
            continue
        for field, value in asdict(CameraConfig(prefix=f"cam{camera_id}")).items():
            if field not in raw["cameras"][key]:
                raw["cameras"][key][field] = value
                changed = True

    if changed:
        save_config(raw)
    return raw


def save_config(config: dict) -> None:
    ensure_dirs()
    with CONFIG_PATH.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)


def capture_directory(prefix: str) -> Path:
    path = CAPTURES_DIR / prefix
    path.mkdir(parents=True, exist_ok=True)
    return path


def latest_capture_name(prefix: str) -> str | None:
    files = list(capture_directory(prefix).glob("*.jpg"))
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_mtime).name


def parse_interval_minutes(value: str | int | None) -> int:
    try:
        return max(1, int(value or 10))
    except (TypeError, ValueError):
        return 10


def normalize_capture_resolution(value: str | None) -> str:
    return value if value in CAPTURE_RESOLUTIONS else DEFAULT_CAPTURE_RESOLUTION


def capture_image(camera_id: int, output_path: Path, width: int, height: int) -> subprocess.CompletedProcess:
    cmd = [
        "rpicam-still",
        "--camera",
        str(camera_id),
        "-n",
        "-t",
        "1000",
        "--width",
        str(width),
        "--height",
        str(height),
        "-o",
        str(output_path),
    ]
    return subprocess.run(cmd, capture_output=True, text=True)


def capture_preview(camera_id: int) -> tuple[bool, str | None]:
    output_path = PREVIEWS_DIR / f"cam{camera_id}.jpg"
    result = capture_image(camera_id, output_path, PREVIEW_CARD_WIDTH, PREVIEW_CARD_HEIGHT)
    if result.returncode != 0:
        return False, result.stderr or result.stdout
    return True, None


def preview_stream_frames(camera_id: int, width: int, height: int):
    cmd = [
        "rpicam-vid",
        "--camera",
        str(camera_id),
        "-n",
        "-t",
        "0",
        "--width",
        str(width),
        "--height",
        str(height),
        "--codec",
        "mjpeg",
        "--output",
        "-",
    ]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    buffer = b""

    try:
        while True:
            if process.stdout is None:
                break

            chunk = process.stdout.read(4096)
            if not chunk:
                break

            buffer += chunk
            while True:
                start = buffer.find(b"\xff\xd8")
                if start == -1:
                    buffer = buffer[-2:]
                    break

                end = buffer.find(b"\xff\xd9", start + 2)
                if end == -1:
                    buffer = buffer[start:]
                    break

                frame = buffer[start:end + 2]
                buffer = buffer[end + 2:]
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                )
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)


def capture_sequence(camera_id: int, config: dict) -> tuple[bool, str | None, str | None]:
    camera = config["cameras"][str(camera_id)]
    camera["sequence"] += 1
    filename = f"{camera['prefix']}_{camera['sequence']:06d}.jpg"
    output_path = capture_directory(camera["prefix"]) / filename
    resolution = normalize_capture_resolution(camera.get("capture_resolution"))
    width, height = CAPTURE_RESOLUTIONS[resolution]
    result = capture_image(camera_id, output_path, width, height)
    if result.returncode != 0:
        camera["sequence"] -= 1
        error_msg = result.stderr or result.stdout or f"rpicam-still exited with code {result.returncode}"
        print(f"[ERROR] Camera {camera_id} capture failed: {error_msg}")
        return False, None, error_msg
    save_config(config)
    print(f"[OK] Camera {camera_id} captured: {filename}")
    return True, filename, None


class CaptureScheduler:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last_run: Dict[int, float] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.is_set():
            config = load_config()
            now = time.time()
            for camera_id in CAMERA_IDS:
                camera = config["cameras"][str(camera_id)]
                if not camera["enabled"]:
                    continue
                try:
                    interval_minutes = max(1, int(camera.get("interval_minutes", 10)))
                    interval_seconds = interval_minutes * 60
                except (ValueError, TypeError):
                    print(f"[WARN] Invalid interval for camera {camera_id}, skipping")
                    continue
                last_run = self._last_run.get(camera_id, 0)
                if now - last_run < interval_seconds:
                    continue
                with self._lock:
                    success, filename, error = capture_sequence(camera_id, config)
                    if success:
                        self._last_run[camera_id] = time.time()
                        print(f"[SCHEDULER] Camera {camera_id} auto-captured: {filename}")
                    else:
                        print(f"[SCHEDULER] Camera {camera_id} failed: {error}")
            self._stop.wait(2)


app = Flask(__name__)
scheduler = CaptureScheduler()


@app.route("/")
def index():
    config = load_config()
    cameras = []
    for camera_id in CAMERA_IDS:
        camera = config["cameras"][str(camera_id)]
        cameras.append(
            {
                "id": camera_id,
                "prefix": camera["prefix"],
                "interval_minutes": camera["interval_minutes"],
                "enabled": camera["enabled"],
                "sequence": camera["sequence"],
                "capture_resolution": normalize_capture_resolution(camera.get("capture_resolution")),
                "latest_capture": latest_capture_name(camera["prefix"]),
            }
        )
    return render_template(
        "index.html",
        cameras=cameras,
        capture_resolutions=list(CAPTURE_RESOLUTIONS.keys()),
    )


@app.post("/config")
def update_config():
    config = load_config()
    for camera_id in CAMERA_IDS:
        key = str(camera_id)
        prefix = request.form.get(f"prefix_{camera_id}", f"cam{camera_id}").strip() or f"cam{camera_id}"
        interval = request.form.get(f"interval_{camera_id}", "10").strip()
        enabled = request.form.get(f"enabled_{camera_id}") == "on"
        capture_resolution = request.form.get(f"capture_resolution_{camera_id}", DEFAULT_CAPTURE_RESOLUTION)
        config["cameras"][key]["prefix"] = prefix
        config["cameras"][key]["interval_minutes"] = parse_interval_minutes(interval)
        config["cameras"][key]["enabled"] = enabled
        config["cameras"][key]["capture_resolution"] = normalize_capture_resolution(capture_resolution)
    config["config_revision"] = int(config.get("config_revision", 0)) + 1
    save_config(config)
    return redirect(url_for("index"))


@app.post("/capture/<int:camera_id>")
def capture_now(camera_id: int):
    config = load_config()
    success, filename, error = capture_sequence(camera_id, config)
    return jsonify({"success": success, "filename": filename, "error": error})


@app.post("/reset-sequence/<int:camera_id>")
def reset_sequence(camera_id: int):
    config = load_config()
    config["cameras"][str(camera_id)]["sequence"] = 0
    save_config(config)
    return jsonify({"success": True, "sequence": 0})


@app.post("/preview/<int:camera_id>")
def preview(camera_id: int):
    success, error = capture_preview(camera_id)
    return jsonify({"success": success, "error": error, "image": f"cam{camera_id}.jpg"})


@app.get("/preview-stream/<int:camera_id>")
def preview_stream(camera_id: int):
    mode = request.args.get("mode", "card")
    if mode == "modal":
        width = PREVIEW_MODAL_WIDTH
        height = PREVIEW_MODAL_HEIGHT
    else:
        width = PREVIEW_CARD_WIDTH
        height = PREVIEW_CARD_HEIGHT

    return Response(
        preview_stream_frames(camera_id, width, height),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/captures/<prefix>/<path:filename>")
def serve_capture(prefix: str, filename: str):
    return send_from_directory(capture_directory(prefix), filename)


@app.get("/previews/<path:filename>")
def serve_preview(filename: str):
    return send_from_directory(PREVIEWS_DIR, filename)


@app.get("/health")
def health():
    return jsonify({"ok": True})


@app.get("/latest-captures")
def latest_captures():
    """Return the latest capture and config info for all cameras"""
    config = load_config()
    captures = {}
    for camera_id in CAMERA_IDS:
        camera = config["cameras"][str(camera_id)]
        latest = latest_capture_name(camera["prefix"])
        captures[str(camera_id)] = {
            "prefix": camera["prefix"],
            "filename": latest,
            "sequence": camera["sequence"],
            "enabled": camera["enabled"],
            "interval_minutes": camera["interval_minutes"],
            "capture_resolution": normalize_capture_resolution(camera.get("capture_resolution")),
        }
    return jsonify({
        "config_revision": int(config.get("config_revision", 0)),
        "cameras": captures,
    })


if __name__ == "__main__":
    ensure_dirs()
    scheduler.start()
    try:
        app.run(host="0.0.0.0", port=5000, debug=False)
    finally:
        scheduler.stop()
