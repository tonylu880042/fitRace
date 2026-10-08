"""System Admin "Download backup" button: real markup + real handler, and
translations in both inline dictionaries.

Comments are stripped before asserting so a nearby comment mentioning the same
words can never satisfy a test once the real markup/code is deleted."""

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"
KEYS = [
    "button.download_backup",
    "message.backup_downloaded",
    "message.backup_download_failed",
]


def _page() -> str:
    return (STATIC_DIR / "systemAdmin.html").read_text(encoding="utf-8")


def _markup(source: str) -> str:
    html = source[: source.index("<script>")]
    return re.sub(r"<!--.*?-->", "", html, flags=re.S)


def _script(source: str) -> str:
    start = source.index("<script>") + len("<script>")
    code = source[start : source.index("</script>", start)]
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    return "\n".join(re.sub(r"(^|\s)//.*$", "", ln) for ln in code.splitlines())


def _function_body(code: str, signature: str) -> str:
    start = code.index(signature)
    brace = code.index("{", start)
    depth = 0
    for i in range(brace, len(code)):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                return code[brace : i + 1]
    raise AssertionError("unbalanced function body")


def test_download_button_markup_is_wired_and_translated():
    buttons = re.findall(r"<button\b[^>]*>", _markup(_page()))
    matches = [b for b in buttons if 'id="btn-download-backup"' in b]
    assert len(matches) == 1
    button = matches[0]
    assert 'onclick="downloadBackup()"' in button
    assert 'data-i18n="button.download_backup"' in button


def test_handler_fetches_the_zip_with_admin_headers_and_downloads_a_blob():
    body = _function_body(_script(_page()), "async function downloadBackup(")
    assert 'fetch("/api/system/backup.zip"' in body
    assert "headers: adminHeaders()" in body
    assert "response.ok" in body
    assert "response.blob()" in body
    assert "URL.createObjectURL(" in body
    assert "URL.revokeObjectURL(" in body
    assert ".download =" in body
    assert 't("message.backup_downloaded")' in body
    assert 't("message.backup_download_failed"' in body


def _dictionaries(source: str):
    code = _script(source)
    en_start = code.index('"en-US": {')
    zh_start = code.index('dictionaries["zh-TW"] = {')
    return code[en_start:zh_start], code[zh_start:]


def test_new_keys_exist_in_both_dictionaries():
    en, zh = _dictionaries(_page())
    for key in KEYS:
        assert f'"{key}":' in en, f"{key} missing from en-US"
        assert f'"{key}":' in zh, f"{key} missing from zh-TW"


def test_zh_translations_are_not_english_copies():
    en, zh = _dictionaries(_page())
    for key in KEYS:
        zh_value = re.search(rf'"{re.escape(key)}":\s*"([^"]*)"', zh).group(1)
        en_value = re.search(rf'"{re.escape(key)}":\s*"([^"]*)"', en).group(1)
        assert zh_value != en_value
