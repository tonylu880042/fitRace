import os
import struct
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLYMOUTH_ROOT = PROJECT_ROOT / "deploy_update" / "plymouth"
THEME_ROOT = PLYMOUTH_ROOT / "fitrace"


def test_fitrace_splash_is_full_hd_png():
    splash = THEME_ROOT / "splash.png"

    with splash.open("rb") as image_file:
        assert image_file.read(8) == b"\x89PNG\r\n\x1a\n"
        chunk_length = struct.unpack(">I", image_file.read(4))[0]
        assert image_file.read(4) == b"IHDR"
        width, height = struct.unpack(">II", image_file.read(8))
        bit_depth, color_type = struct.unpack(">BB", image_file.read(2))

    assert chunk_length == 13
    assert (width, height) == (1920, 1080)
    assert (bit_depth, color_type) == (8, 2)


def test_fitrace_plymouth_theme_and_installer_are_complete():
    descriptor = (THEME_ROOT / "fitrace.plymouth").read_text(encoding="utf-8")
    script = (THEME_ROOT / "fitrace.script").read_text(encoding="utf-8")
    installer = PLYMOUTH_ROOT / "install_fitrace_splash.sh"
    installer_source = installer.read_text(encoding="utf-8")

    assert "Name=FitRace Studio" in descriptor
    assert "ModuleName=script" in descriptor
    assert "ImageDir=/usr/share/plymouth/themes/fitrace" in descriptor
    assert 'theme_image = Image("splash.png");' in script
    assert 'set_theme "fitrace"' in installer_source
    assert 'set_theme "pix"' in installer_source
    assert "/usr/sbin/update-initramfs -u -k all" in installer_source
    assert os.access(installer, os.X_OK)

    subprocess.run(["bash", "-n", installer], check=True)
