from __future__ import annotations

import os
import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable


def run_command(command: list[str]):
    subprocess.run(command, check=True, timeout=30)


def check_hub_health(url: str = "http://localhost:8000/health") -> bool:
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            body = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return False
    return body.get("status") == "ok"


def wait_for_health(
    health_check: Callable[[], bool],
    retries: int = 15,
    interval_sec: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    for attempt in range(retries):
        if health_check():
            return True
        if attempt < retries - 1:
            sleep(interval_sec)
    return False


def _point_symlink(link_path: Path, target: Path) -> None:
    tmp_link = link_path.with_name(f".{link_path.name}.tmp")
    if tmp_link.exists() or tmp_link.is_symlink():
        tmp_link.unlink()
    tmp_link.symlink_to(target)
    os.replace(tmp_link, link_path)


def _release_version(release_path: Path) -> str:
    name = release_path.name
    return name[len("hub-") :] if name.startswith("hub-") else name


def _write_pending_verify_marker(
    marker_path: Path, previous_target: Path | None, target: Path
) -> None:
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "previous": str(previous_target) if previous_target is not None else None,
        "applied": target.name,
        "applied_at_epoch_ms": int(time.time() * 1000),
    }
    tmp = marker_path.with_suffix(marker_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, marker_path)  # atomic on POSIX -- no half-written marker


def _clear_pending_verify_marker(marker_path: Path) -> None:
    try:
        marker_path.unlink()
    except FileNotFoundError:
        pass


def apply_hub_update(
    cache_dir: str | Path,
    release_root: str | Path,
    current_link: str | Path,
    service_name: str,
    restart: bool = True,
    runner: Callable[[list[str]], None] = run_command,
    health_check: Callable[[], bool] = check_hub_health,
    health_check_retries: int = 15,
    health_check_interval_sec: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    pending_verify_marker_path: str | Path | None = None,
) -> dict:
    cache_dir = Path(cache_dir)
    release_root = Path(release_root)
    current_link = Path(current_link)
    marker_path = (
        Path(pending_verify_marker_path)
        if pending_verify_marker_path is not None
        else current_link.parent / "pending-verify.json"
    )
    version = (cache_dir / "active-hub-version").read_text().strip()
    source = cache_dir / "installed" / f"hub-{version}"
    if not source.exists():
        raise FileNotFoundError(f"staged hub release not found: {source}")

    # Capture the release we are about to replace, before it is overwritten,
    # so a bad update can be rolled back to it.
    previous_target = current_link.resolve() if current_link.is_symlink() else None

    release_root.mkdir(parents=True, exist_ok=True)
    target = release_root / f"hub-{version}"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)

    # Persist what we're about to do *before* the symlink moves, so a crash
    # or reboot mid-verification leaves fitrace-guard enough to recover from.
    _write_pending_verify_marker(marker_path, previous_target, target)

    _point_symlink(current_link, target)

    if not restart:
        _clear_pending_verify_marker(marker_path)
        return {
            "state": "applied",
            "version": version,
            "release_path": str(target),
            "current_link": str(current_link),
            "service_name": service_name,
            "service_restart": "not_run",
            "applied_at_epoch_ms": int(time.time() * 1000),
        }

    runner(["systemctl", "restart", service_name])

    if wait_for_health(
        health_check,
        retries=health_check_retries,
        interval_sec=health_check_interval_sec,
        sleep=sleep,
    ):
        _clear_pending_verify_marker(marker_path)
        return {
            "state": "applied",
            "version": version,
            "release_path": str(target),
            "current_link": str(current_link),
            "service_name": service_name,
            "service_restart": "restarted",
            "applied_at_epoch_ms": int(time.time() * 1000),
        }

    if previous_target is not None:
        _point_symlink(current_link, previous_target)
        runner(["systemctl", "restart", service_name])
        _clear_pending_verify_marker(marker_path)
        return {
            "state": "rolled_back",
            "version": version,
            "rolled_back_to": _release_version(previous_target),
            "release_path": str(target),
            "current_link": str(current_link),
            "service_name": service_name,
            "service_restart": "restarted",
            "applied_at_epoch_ms": int(time.time() * 1000),
        }

    # In-process verification failed and there is nothing to roll back to.
    # Leave the marker pending -- its null `previous` already tells a later
    # guard reader there is no safe rollback target either.
    return {
        "state": "failed",
        "version": version,
        "release_path": str(target),
        "current_link": str(current_link),
        "service_name": service_name,
        "service_restart": "restarted",
        "applied_at_epoch_ms": int(time.time() * 1000),
    }


def main():
    result = apply_hub_update(
        cache_dir=os.getenv("FITRACE_UPDATE_CACHE_DIR", "/tmp/fitrace-update-cache"),
        release_root=os.getenv("FITRACE_RELEASE_ROOT", "/opt/fitracestudio/releases"),
        current_link=os.getenv("FITRACE_CURRENT_LINK", "/opt/fitracestudio/current"),
        service_name=os.getenv("FITRACE_HUB_SERVICE", "fitracestudio-hub.service"),
        restart=os.getenv("FITRACE_UPDATE_RESTART_SERVICE", "1") != "0",
    )
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["state"] == "applied" else 1)


if __name__ == "__main__":
    main()
