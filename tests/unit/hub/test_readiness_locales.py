"""Every readiness code the hub can emit has a readiness.<code> message in
every locale, with exactly the placeholders its params provide."""

import ast
import re
from pathlib import Path

from hub_server.infrastructure.fastapi import app as app_module
from hub_server.infrastructure.locales import SUPPORTED_LOCALES, load_locale

CODE_PARAMS = {
    # issues / warnings
    "state_not_ready": {"state"},
    "no_config": set(),
    "target_invalid": set(),
    "duration_invalid": set(),
    "clock_skew": {"edge", "offset_ms"},
    "no_registered_athletes": set(),
    "no_stations_assigned": set(),
    "group_equipment_unknown": {"station"},
    "group_equipment_unmatched": {"station", "equipment"},
    "group_empty": {"group", "types"},
    "unassigned": {"station"},
    "device_missing": {"station"},
    "edge_offline": {"station"},
    "no_data": {"station"},
    "stale": {"station"},
    "generic": {"station"},
    "team_needs_two": set(),
    "team_member_needs_name": set(),
    "relay_needs_name": {"station"},
    "relay_wrong_size": {"station", "legs", "count"},
    "sound_disabled": set(),
    # checks
    "state_ok": set(),
    "target_ok": set(),
    "registrations_ok": set(),
    "teams_ok": set(),
    "stations_ok": set(),
    "sound_ok": set(),
    "clock_ok": set(),
    "target_missing": set(),
    "clock_skew_summary": {"count"},
    "registrations_none": set(),
    "registrations_count": {"count"},
    "registrations_anonymous": set(),
    "stations_none_assigned": set(),
    "groups_unmatched": {"count"},
    "groups_empty": {"count"},
    "groups_ok": set(),
    "stations_need_attention": {"count", "stations"},
    "stations_none_online": set(),
    "stations_partial": {"online", "offline"},
    "teams_review": set(),
    "teams_ready": {"count"},
    "relay_teams_review": set(),
    "relay_teams_ready": {"count"},
    "teams_individual": set(),
    "sound_silent": set(),
}


def _codes_emitted_by_the_hub() -> set[str]:
    tree = ast.parse(Path(app_module.__file__).read_text(encoding="utf-8"))
    codes: set[str] = set(app_module.STATION_REASON_CODES) | {"generic"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        candidate = None
        if node.func.id == "readiness_item" and node.args:
            candidate = node.args[0]
        elif node.func.id == "build_check":
            if len(node.args) >= 3:
                candidate = node.args[2]
            for keyword in node.keywords:
                if keyword.arg == "code":
                    candidate = keyword.value
        if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
            codes.add(candidate.value)
    return codes


def test_the_code_table_matches_what_the_hub_emits():
    assert set(CODE_PARAMS) == _codes_emitted_by_the_hub()


def test_every_code_has_a_message_in_every_locale_with_matching_placeholders():
    for locale in SUPPORTED_LOCALES:
        messages = load_locale(locale)["messages"]
        for code, params in CODE_PARAMS.items():
            key = f"readiness.{code}"
            assert key in messages, f"{locale} lacks {key}"
            assert set(re.findall(r"\{(\w+)\}", messages[key])) == params, (
                locale,
                key,
            )


def test_en_us_and_zh_tw_are_real_text_in_their_own_language():
    en = load_locale("en-US")["messages"]
    zh = load_locale("zh-TW")["messages"]
    for code in CODE_PARAMS:
        key = f"readiness.{code}"
        assert not re.search(r"[一-鿿]", en[key]), key
        assert re.search(r"[一-鿿]", zh[key]), key
    assert en["readiness.no_registered_athletes"] == (
        "Register at least one athlete before starting."
    )
