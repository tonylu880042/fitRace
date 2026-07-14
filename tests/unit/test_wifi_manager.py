from fitrace_common import wifi_manager


def test_list_networks_parses_terse_output_and_dedupes(monkeypatch):
    scan_output = (
        " :70:WPA2:UCM_DEMO\n"
        "*:82:WPA2:fitRace26\n"
        " :55:WPA2:fitRace26\n"
        " :40::OpenNet\n"
        " :60:WPA2:has\\:colon\n"
        " :50:WPA2:\n"  # hidden SSID dropped
    )
    profile_output = "fitRace26:802-11-wireless\nWired connection 1:802-3-ethernet\n"

    def fake_run(args, timeout=45):
        return profile_output if args[-1] == "show" else scan_output

    monkeypatch.setattr(wifi_manager, "_run_nmcli", fake_run)

    networks = wifi_manager.list_networks()

    by_ssid = {net["ssid"]: net for net in networks}
    assert set(by_ssid) == {"UCM_DEMO", "fitRace26", "OpenNet", "has\\:colon"}
    assert by_ssid["fitRace26"] == {
        "ssid": "fitRace26", "signal": 82, "secured": True, "active": True, "saved": True,
    }
    assert by_ssid["OpenNet"]["secured"] is False
    assert by_ssid["UCM_DEMO"]["saved"] is False
    assert networks[0]["ssid"] == "fitRace26"  # active first
