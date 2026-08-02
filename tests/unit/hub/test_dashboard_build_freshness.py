from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def _extract_function(source: str, signature: str, next_marker: str) -> str:
    start = source.index(signature)
    end = source.index(next_marker, start)
    return source[start:end]


def _strip_full_line_comments(block: str) -> str:
    """Drop lines that are entirely a `//` comment, so an assertion on a
    code expression can't be satisfied by a comment mentioning the same
    text instead of the real code."""
    return "\n".join(
        line for line in block.splitlines() if not line.strip().startswith("//")
    )


def test_dashboard_has_a_version_badge():
    source = _read("index.html")
    assert 'id="hub-version"' in source
    assert 'class="version-badge"' in source


def test_dashboard_has_a_stale_page_notice_hidden_by_default():
    source = _read("index.html")
    notice_start = source.index('id="stale-page-notice"')
    # The opening tag is a handful of attributes back from the id.
    tag_start = source.rindex("<div", 0, notice_start)
    tag_end = source.index(">", notice_start)
    tag = source[tag_start:tag_end]
    assert "hidden" in tag
    assert 'data-i18n="dashboard.stale_notice"' in tag


def test_dashboard_load_hub_version_remembers_the_starting_fingerprint():
    source = _read("index.html")
    fn = _extract_function(
        source,
        "async function loadHubVersion()",
        "function showStalePageNotice()",
    )
    fn = _strip_full_line_comments(fn)

    assert '"/api/system/version"' in fn
    # The real assignment that captures the tab's starting fingerprint --
    # not just a mention of the variable name.
    assert "initialBuildFingerprint = data.build_fingerprint" in fn


def test_dashboard_staleness_check_compares_against_the_stored_starting_fingerprint():
    source = _read("index.html")
    fn = _extract_function(
        source,
        "async function checkBuildFreshness()",
        "let dashboardInitialized = false;",
    )
    fn = _strip_full_line_comments(fn)

    # This is the crux of the feature: the server's current fingerprint must
    # be compared against the fingerprint captured when the tab first
    # loaded -- not against itself, and not against some other value.
    assert "data.build_fingerprint !== initialBuildFingerprint" in fn
    # The comparison must actually be what gates showing the notice.
    guard_index = fn.index("data.build_fingerprint !== initialBuildFingerprint")
    call_index = fn.index("showStalePageNotice()")
    assert guard_index < call_index


def test_dashboard_freshness_check_self_heals_when_no_baseline_yet_recorded():
    # If the single loadHubVersion() call at page load fails (a venue Wi-Fi
    # blip is enough), initialBuildFingerprint is never set, and the old
    # `if (!initialBuildFingerprint) return;` guard disabled staleness
    # detection for the rest of the tab's life. checkBuildFreshness() must
    # instead adopt its own successful response as the baseline instead of
    # bailing out forever.
    source = _read("index.html")
    fn = _extract_function(
        source,
        "async function checkBuildFreshness()",
        "let dashboardInitialized = false;",
    )
    fn = _strip_full_line_comments(fn)

    # The permanent bail-out guard must be gone.
    assert "if (!initialBuildFingerprint) return;" not in fn

    # A good response with no baseline yet recorded must adopt it.
    assert "initialBuildFingerprint === null" in fn
    assert "initialBuildFingerprint = data.build_fingerprint;" in fn


def test_dashboard_freshness_check_does_not_flag_the_pass_that_seeds_the_baseline():
    # Adopting a fresh baseline must not immediately compare it against
    # itself and fire the stale notice on the very same pass -- the seeding
    # branch must return before reaching the comparison.
    source = _read("index.html")
    fn = _extract_function(
        source,
        "async function checkBuildFreshness()",
        "let dashboardInitialized = false;",
    )
    fn = _strip_full_line_comments(fn)

    seed_index = fn.index("initialBuildFingerprint = data.build_fingerprint;")
    seed_return_index = fn.index("return;", seed_index)
    compare_index = fn.index("data.build_fingerprint !== initialBuildFingerprint")
    notice_index = fn.index("showStalePageNotice()")
    assert seed_index < seed_return_index < compare_index < notice_index


def test_dashboard_staleness_check_is_wired_into_the_existing_fetchNodes_poll():
    source = _read("index.html")
    fn = _extract_function(
        source,
        "async function fetchNodes()",
        "function renderStations(data)",
    )
    fn = _strip_full_line_comments(fn)

    assert "checkBuildFreshness();" in fn

    # No second timer was introduced to drive this -- setInterval/setTimeout
    # do not appear anywhere in fetchNodes(), loadHubVersion(), or
    # checkBuildFreshness().
    for name, marker, stop in [
        ("fetchNodes", "async function fetchNodes()", "function renderStations(data)"),
        (
            "checkBuildFreshness",
            "async function checkBuildFreshness()",
            "let dashboardInitialized = false;",
        ),
    ]:
        body = _strip_full_line_comments(_extract_function(source, marker, stop))
        assert "setInterval" not in body, f"{name} should not add its own timer"
        assert "setTimeout" not in body, f"{name} should not add its own timer"


def test_dashboard_staleness_notice_never_triggers_a_reload():
    source = _read("index.html")
    for marker, stop in [
        ("async function checkBuildFreshness()", "let dashboardInitialized = false;"),
        (
            "function showStalePageNotice()",
            "// Re-checked on the dashboard's existing poll cadence",
        ),
    ]:
        body = _extract_function(source, marker, stop)
        assert ".reload(" not in body
        assert "location.reload" not in body


def test_dashboard_stale_notice_key_present_in_default_locale_fallback_text():
    source = _read("index.html")
    assert (
        "This page is showing an outdated version. Please reload." in source
    ), "the data-i18n element should carry readable fallback text, not be empty"
