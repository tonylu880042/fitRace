"""Guard against the packaging version and the runtime version drifting apart.

fitrace-studio declares its version in two places: ``pyproject.toml``
(``[project] version``, used for packaging/release tooling) and
``fitrace_common.version.APP_VERSION`` (the literal the running app actually
reports via ``/health`` etc. -- see that module's docstring for why it is a
hand-maintained literal rather than something derived from installed
distribution metadata at runtime). Nothing enforced that a release bump to
one also bumped the other, so a real release once shipped reporting the
previous version. This test fails whenever the two fall out of sync.
"""

import tomllib
from pathlib import Path

from fitrace_common.version import APP_VERSION

PYPROJECT_PATH = Path(__file__).resolve().parents[2] / "pyproject.toml"


def test_pyproject_version_matches_app_version():
    with PYPROJECT_PATH.open("rb") as f:
        pyproject = tomllib.load(f)

    pyproject_version = pyproject["project"]["version"]

    assert pyproject_version == APP_VERSION
