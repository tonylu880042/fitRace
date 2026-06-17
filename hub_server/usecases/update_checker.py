from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse
from urllib.request import urlopen


def fetch_url_bytes(url: str, timeout_sec: int) -> bytes:
    with urlopen(url, timeout=timeout_sec) as response:
        return response.read()


def fetch_url_text(url: str, timeout_sec: int) -> str:
    return fetch_url_bytes(url, timeout_sec).decode("utf-8")


def verify_ed25519_signature(manifest_text: str, signature_text: str | bytes, public_key_path: str) -> bool:
    signature_bytes = signature_text if isinstance(signature_text, bytes) else signature_text.encode("utf-8")
    with tempfile.NamedTemporaryFile() as manifest_file, tempfile.NamedTemporaryFile() as signature_file:
        manifest_file.write(manifest_text.encode("utf-8"))
        manifest_file.flush()
        signature_file.write(signature_bytes)
        signature_file.flush()
        result = subprocess.run(
            [
                "openssl",
                "pkeyutl",
                "-verify",
                "-rawin",
                "-pubin",
                "-inkey",
                public_key_path,
                "-in",
                manifest_file.name,
                "-sigfile",
                signature_file.name,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    return result.returncode == 0


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version or "0"))


def extract_zstd_tar(source: Path, target: Path):
    command = f"zstd -dc {shlex.quote(str(source))} | tar -xf - -C {shlex.quote(str(target))}"
    subprocess.run(command, shell=True, check=True, timeout=60)


class UpdateChecker:
    def __init__(
        self,
        manifest_url: str,
        signature_url: str,
        current_version: str,
        fetch_text: Callable[[str, int], str] = fetch_url_text,
        fetch_bytes: Callable[[str, int], bytes] = fetch_url_bytes,
        public_key_path: str = "",
        verify_signature: Callable[[str, str | bytes, str], bool] = verify_ed25519_signature,
        cache_dir: str = "/tmp/fitrace-update-cache",
        extract_archive: Callable[[Path, Path], None] = extract_zstd_tar,
        timeout_sec: int = 10,
    ):
        self.manifest_url = manifest_url
        self.signature_url = signature_url
        self.current_version = current_version
        self.fetch_text = fetch_text
        self.fetch_bytes = fetch_bytes
        self.public_key_path = public_key_path
        self.verify_signature = verify_signature
        self.cache_dir = cache_dir
        self.extract_archive = extract_archive
        self.timeout_sec = timeout_sec
        self._status = {
            "state": "never_checked",
            "update_available": False,
            "current_version": current_version,
            "latest_hub_version": None,
            "latest_edge_version": None,
            "checked_at_epoch_ms": None,
            "manifest_url": manifest_url,
            "signature_url": signature_url,
            "public_key_path": public_key_path,
            "cache_dir": cache_dir,
            "error": None,
        }

    def status(self) -> dict:
        return dict(self._status)

    def check(self) -> dict:
        if not self.manifest_url:
            raise ValueError("update manifest URL is not configured")

        checked_at_epoch_ms = int(time.time() * 1000)
        try:
            manifest_text = self.fetch_text(self.manifest_url, self.timeout_sec)
            manifest = json.loads(manifest_text)
            signature_verified = False
            if self.signature_url and self.public_key_path:
                signature_text = self.fetch_bytes(self.signature_url, self.timeout_sec)
                signature_verified = self.verify_signature(
                    manifest_text,
                    signature_text,
                    self.public_key_path,
                )
                if not signature_verified:
                    raise ValueError("manifest signature verification failed")

            hub_version = manifest.get("components", {}).get("hub", {}).get("version")
            edge_version = manifest.get("components", {}).get("edge", {}).get("version")
            update_available = version_key(hub_version or "") > version_key(self.current_version)

            self._status = {
                "state": "available" if update_available else "current",
                "update_available": update_available,
                "current_version": self.current_version,
                "latest_hub_version": hub_version,
                "latest_edge_version": edge_version,
                "checked_at_epoch_ms": checked_at_epoch_ms,
                "manifest_url": self.manifest_url,
                "signature_url": self.signature_url,
                "public_key_path": self.public_key_path,
                "signature_checked": bool(self.signature_url and self.public_key_path),
                "signature_verified": signature_verified,
                "manifest": manifest,
                "error": None,
            }
        except Exception as exc:
            self._status = {
                **self._status,
                "state": "error",
                "update_available": False,
                "checked_at_epoch_ms": checked_at_epoch_ms,
                "signature_verified": False,
                "error": str(exc),
            }
        return self.status()

    def install_hub(self) -> dict:
        if self._status.get("state") != "downloaded":
            self.download()
        if self._status.get("state") == "error":
            return self.status()

        installed_at_epoch_ms = int(time.time() * 1000)
        try:
            hub_artifact = self._status.get("artifacts", {}).get("hub")
            if not hub_artifact or not hub_artifact.get("sha256_verified"):
                raise ValueError("hub artifact is not downloaded")

            version = hub_artifact.get("version") or self._status.get("manifest", {}).get("release_version")
            artifact_path = Path(hub_artifact["path"])
            if not artifact_path.exists():
                raise ValueError("hub artifact is not downloaded")

            install_path = Path(self.cache_dir) / "installed" / f"hub-{version}"
            if install_path.exists():
                shutil.rmtree(install_path)
            install_path.mkdir(parents=True)
            self.extract_archive(artifact_path, install_path)

            active_marker = Path(self.cache_dir) / "active-hub-version"
            active_marker.write_text(str(version))
            self._status = {
                **self._status,
                "state": "hub_installed",
                "installed_at_epoch_ms": installed_at_epoch_ms,
                "hub_install": {
                    "version": version,
                    "path": str(install_path),
                    "active_marker": str(active_marker),
                    "service_restart": "not_run",
                },
                "error": None,
            }
        except Exception as exc:
            self._status = {
                **self._status,
                "state": "error",
                "installed_at_epoch_ms": installed_at_epoch_ms,
                "error": str(exc),
            }
        return self.status()

    def download(self) -> dict:
        if "manifest" not in self._status or self._status.get("state") == "error":
            self.check()
        if self._status.get("state") == "error":
            return self.status()

        checked_at_epoch_ms = int(time.time() * 1000)
        try:
            manifest = self._status["manifest"]
            release_version = manifest.get("release_version") or "unknown"
            release_dir = Path(self.cache_dir) / release_version
            release_dir.mkdir(parents=True, exist_ok=True)
            artifacts = {}

            for component, info in manifest.get("components", {}).items():
                url = info.get("artifact_url")
                expected_sha256 = info.get("sha256")
                if not url or not expected_sha256:
                    continue

                data = self.fetch_bytes(url, self.timeout_sec)
                actual_sha256 = hashlib.sha256(data).hexdigest()
                if actual_sha256 != expected_sha256:
                    raise ValueError(f"{component} artifact checksum mismatch")

                filename = os.path.basename(urlparse(url).path)
                path = release_dir / filename
                path.write_bytes(data)
                artifacts[component] = {
                    "version": info.get("version"),
                    "artifact_url": url,
                    "path": str(path),
                    "bytes": len(data),
                    "sha256": actual_sha256,
                    "sha256_verified": True,
                }

            self._status = {
                **self._status,
                "state": "downloaded",
                "downloaded_at_epoch_ms": checked_at_epoch_ms,
                "cache_dir": self.cache_dir,
                "artifacts": artifacts,
                "error": None,
            }
        except Exception as exc:
            self._status = {
                **self._status,
                "state": "error",
                "downloaded_at_epoch_ms": checked_at_epoch_ms,
                "error": str(exc),
            }
        return self.status()
