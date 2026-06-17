import pytest
import hashlib

from hub_server.usecases.update_checker import UpdateChecker


def test_update_checker_detects_available_release_and_fetches_signature():
    calls = []
    verify_calls = []
    manifest_text = """
    {
      "schema_version": 1,
      "release_version": "0.1.1",
      "components": {
        "hub": {
          "version": "0.1.1",
          "artifact_url": "https://example.com/hub.tar.zst",
          "sha256": "hub-sha"
        },
        "edge": {
          "version": "0.1.1",
          "artifact_url": "https://example.com/edge.tar.zst",
          "sha256": "edge-sha"
        }
      }
    }
    """

    def fetch_text(url, timeout_sec):
        calls.append((url, timeout_sec))
        return manifest_text

    def fetch_bytes(url, timeout_sec):
        calls.append((url, timeout_sec))
        return b"TEST-SIGNATURE"

    checker = UpdateChecker(
        manifest_url="https://example.com/channels/stable/manifest.json",
        signature_url="https://example.com/channels/stable/manifest.json.sig",
        current_version="0.1.0",
        fetch_text=fetch_text,
        fetch_bytes=fetch_bytes,
        public_key_path="/tmp/release-public.pem",
        verify_signature=lambda manifest, signature, key_path: verify_calls.append(
            (manifest, signature, key_path)
        ) or True,
    )

    status = checker.check()

    assert status["state"] == "available"
    assert status["update_available"] is True
    assert status["current_version"] == "0.1.0"
    assert status["latest_hub_version"] == "0.1.1"
    assert status["latest_edge_version"] == "0.1.1"
    assert calls == [
        ("https://example.com/channels/stable/manifest.json", 10),
        ("https://example.com/channels/stable/manifest.json.sig", 10),
    ]
    assert verify_calls == [
        (
            manifest_text,
            b"TEST-SIGNATURE",
            "/tmp/release-public.pem",
        )
    ]
    assert status["signature_verified"] is True


def test_update_checker_reports_current_when_versions_match():
    checker = UpdateChecker(
        manifest_url="https://example.com/manifest.json",
        signature_url="https://example.com/manifest.json.sig",
        current_version="0.1.0",
        fetch_text=lambda url, timeout_sec: """
        {
          "release_version": "0.1.0",
          "components": {
            "hub": {"version": "0.1.0"},
            "edge": {"version": "0.1.0"}
          }
        }
        """,
    )

    status = checker.check()

    assert status["state"] == "current"
    assert status["update_available"] is False


def test_update_checker_keeps_error_in_status():
    def fail(url, timeout_sec):
        raise OSError("network down")

    checker = UpdateChecker(
        manifest_url="https://example.com/manifest.json",
        signature_url="https://example.com/manifest.json.sig",
        current_version="0.1.0",
        fetch_text=fail,
    )

    status = checker.check()

    assert status["state"] == "error"
    assert status["update_available"] is False
    assert status["error"] == "network down"


def test_update_checker_rejects_bad_signature():
    checker = UpdateChecker(
        manifest_url="https://example.com/manifest.json",
        signature_url="https://example.com/manifest.json.sig",
        current_version="0.1.0",
        fetch_text=lambda url, timeout_sec: "{}",
        fetch_bytes=lambda url, timeout_sec: b"bad-signature",
        public_key_path="/tmp/release-public.pem",
        verify_signature=lambda manifest, signature, key_path: False,
    )

    status = checker.check()

    assert status["state"] == "error"
    assert status["update_available"] is False
    assert status["signature_verified"] is False
    assert status["error"] == "manifest signature verification failed"


def test_update_checker_requires_manifest_url():
    checker = UpdateChecker(manifest_url="", signature_url="", current_version="0.1.0")

    with pytest.raises(ValueError, match="manifest URL"):
        checker.check()


def test_update_checker_downloads_artifacts_and_verifies_sha256(tmp_path):
    hub_bytes = b"hub artifact"
    edge_bytes = b"edge artifact"
    manifest_text = f"""
    {{
      "release_version": "0.1.1",
      "components": {{
        "hub": {{
          "version": "0.1.1",
          "artifact_url": "https://example.com/releases/0.1.1/fitrace-hub-0.1.1.tar.zst",
          "sha256": "{hashlib.sha256(hub_bytes).hexdigest()}"
        }},
        "edge": {{
          "version": "0.1.1",
          "artifact_url": "https://example.com/releases/0.1.1/fitrace-edge-0.1.1.tar.zst",
          "sha256": "{hashlib.sha256(edge_bytes).hexdigest()}"
        }}
      }}
    }}
    """

    def fetch_bytes(url, timeout_sec):
        if "hub" in url:
            return hub_bytes
        if "edge" in url:
            return edge_bytes
        return b"signature"

    checker = UpdateChecker(
        manifest_url="https://example.com/manifest.json",
        signature_url="https://example.com/manifest.json.sig",
        current_version="0.1.0",
        fetch_text=lambda url, timeout_sec: manifest_text,
        fetch_bytes=fetch_bytes,
        public_key_path="/tmp/release-public.pem",
        verify_signature=lambda manifest, signature, key_path: True,
        cache_dir=str(tmp_path),
    )

    status = checker.download()

    assert status["state"] == "downloaded"
    assert status["artifacts"]["hub"]["sha256_verified"] is True
    assert status["artifacts"]["edge"]["sha256_verified"] is True
    assert (tmp_path / "0.1.1" / "fitrace-hub-0.1.1.tar.zst").read_bytes() == hub_bytes
    assert (tmp_path / "0.1.1" / "fitrace-edge-0.1.1.tar.zst").read_bytes() == edge_bytes


def test_update_checker_rejects_artifact_checksum_mismatch(tmp_path):
    checker = UpdateChecker(
        manifest_url="https://example.com/manifest.json",
        signature_url="https://example.com/manifest.json.sig",
        current_version="0.1.0",
        fetch_text=lambda url, timeout_sec: """
        {
          "release_version": "0.1.1",
          "components": {
            "hub": {
              "version": "0.1.1",
              "artifact_url": "https://example.com/hub.tar.zst",
              "sha256": "wrong"
            }
          }
        }
        """,
        fetch_bytes=lambda url, timeout_sec: b"hub artifact",
        cache_dir=str(tmp_path),
    )

    status = checker.download()

    assert status["state"] == "error"
    assert status["error"] == "hub artifact checksum mismatch"


def test_update_checker_installs_hub_artifact_into_versioned_directory(tmp_path):
    artifact_path = tmp_path / "0.1.1" / "fitrace-hub-0.1.1.tar.zst"
    artifact_path.parent.mkdir()
    artifact_path.write_bytes(b"hub artifact")
    extract_calls = []

    def extract_archive(source, target):
        extract_calls.append((source, target))
        (target / "hub_server").mkdir(parents=True)
        (target / "hub_server" / "main.py").write_text("print('hub')\n")

    checker = UpdateChecker(
        manifest_url="https://example.com/manifest.json",
        signature_url="https://example.com/manifest.json.sig",
        current_version="0.1.0",
        cache_dir=str(tmp_path),
        extract_archive=extract_archive,
    )
    checker._status = {
        "state": "downloaded",
        "manifest": {"release_version": "0.1.1"},
        "artifacts": {
            "hub": {
                "version": "0.1.1",
                "path": str(artifact_path),
                "sha256_verified": True,
            }
        },
    }

    status = checker.install_hub()

    install_path = tmp_path / "installed" / "hub-0.1.1"
    assert status["state"] == "hub_installed"
    assert status["hub_install"]["version"] == "0.1.1"
    assert status["hub_install"]["path"] == str(install_path)
    assert (install_path / "hub_server" / "main.py").read_text() == "print('hub')\n"
    assert (tmp_path / "active-hub-version").read_text() == "0.1.1"
    assert extract_calls == [(artifact_path, install_path)]


def test_update_checker_install_hub_requires_downloaded_artifact(tmp_path):
    checker = UpdateChecker(
        manifest_url="https://example.com/manifest.json",
        signature_url="https://example.com/manifest.json.sig",
        current_version="0.1.0",
        fetch_text=lambda url, timeout_sec: '{"components": {}}',
        cache_dir=str(tmp_path),
    )

    status = checker.install_hub()

    assert status["state"] == "error"
    assert status["error"] == "hub artifact is not downloaded"
