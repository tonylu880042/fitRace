"""CLAUDE.md's i18n section must describe the locale files that really exist
and the completeness policy (docs/zh_cn_i18n_spec.md section 6)."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCALE_DIR = REPO_ROOT / "hub_server" / "infrastructure" / "locales"


def _i18n_section() -> str:
    text = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    match = re.search(r"^## i18n\n(.*?)(?=^## )", text, re.S | re.M)
    assert match, "CLAUDE.md has no '## i18n' section"
    return match.group(1)


def test_section_names_every_real_locale_file_and_no_stale_ones():
    section = _i18n_section()
    for path in sorted(LOCALE_DIR.glob("*.json")):
        assert path.name in section, f"{path.name} not listed in CLAUDE.md i18n"
    assert "zh_tw.json" not in section
    assert "en.json" not in section


def test_section_states_the_completeness_policy():
    section = " ".join(_i18n_section().split())
    assert "en-US, zh-TW and zh-CN" in section
    assert "exactly the same key set" in section
    for partial in ("it", "fr", "de-CH", "sv"):
        assert partial in section
    assert "en-US" in section and "merged" in section
    assert "never has a key en-US lacks" in section
