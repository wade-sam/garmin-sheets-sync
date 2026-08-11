import json
from importlib.metadata import version
from pathlib import Path

from garmin_sheets_sync import __version__

REPOSITORY_ROOT = Path(__file__).parents[1]


def test_package_version_matches_installed_metadata() -> None:
    assert __version__ == version("garmin-sheets-sync")


def test_release_tag_matches_deployment_trigger() -> None:
    config = json.loads((REPOSITORY_ROOT / "release-please-config.json").read_text())
    package = config["packages"]["."]

    assert package["include-v-in-tag"] is True
    assert package["include-component-in-tag"] is False
