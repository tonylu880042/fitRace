from __future__ import annotations

import os
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable


def run_command(command: list[str]):
    subprocess.run(command, check=True, timeout=30)


def apply_hub_update(
    cache_dir: str | Path,
    release_root: str | Path,
    current_link: str | Path,
    service_name: str,
    restart: bool = True,
    runner: Callable[[list[str]], None] = run_command,
) -> dict:
    cache_dir = Path(cache_dir)
    release_root = Path(release_root)
    current_link = Path(current_link)
    version = (cache_dir / "active-hub-version").read_text().strip()
    source = cache_dir / "installed" / f"hub-{version}"
    if not source.exists():
        raise FileNotFoundError(f"staged hub release not found: {source}")

    release_root.mkdir(parents=True, exist_ok=True)
    target = release_root / f"hub-{version}"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)

    tmp_link = current_link.with_name(f".{current_link.name}.tmp")
    if tmp_link.exists() or tmp_link.is_symlink():
        tmp_link.unlink()
    tmp_link.symlink_to(target)
    os.replace(tmp_link, current_link)

    service_restart = "not_run"
    if restart:
        runner(["systemctl", "restart", service_name])
        service_restart = "restarted"

    return {
        "state": "applied",
        "version": version,
        "release_path": str(target),
        "current_link": str(current_link),
        "service_name": service_name,
        "service_restart": service_restart,
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


if __name__ == "__main__":
    main()
