from importlib.metadata import version

from garmin_sheets_sync import __version__


def test_package_version_matches_installed_metadata() -> None:
    assert __version__ == version("garmin-sheets-sync")
