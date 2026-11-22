from types import SimpleNamespace

import pytest

from photo_backer_upper import cli
from photo_backer_upper.automation import ConfigError


def run_args():
    return SimpleNamespace(dry_run=False, delete=False, notify=True, skip_space_check=False)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    archive.mkdir()
    settings = {}
    monkeypatch.setattr(cli, "cmd_export", lambda args: 0)
    monkeypatch.setattr(cli, "main_archive", lambda: archive)
    monkeypatch.setattr(cli.config, "get", lambda name, default=None: settings.get(name, default))
    return tmp_path, settings


def test_run_skips_mirror_without_secondary(setup, monkeypatch, capsys):
    monkeypatch.setattr(cli, "require_tool", lambda *a: pytest.fail("rsync not needed"))
    assert cli.cmd_run(run_args()) == 0
    assert "Skipping mirror: PHOTO_BACKER_UPPER_SECONDARY_DIR is not set" in capsys.readouterr().err


def test_run_skips_mirror_when_drive_not_attached(setup, capsys):
    _tmp, settings = setup
    settings["PHOTO_BACKER_UPPER_SECONDARY_DIR"] = "/Volumes/No Such Drive For Tests/Photos"
    assert cli.cmd_run(run_args()) == 0
    assert "Skipping mirror: Secondary drive not found" in capsys.readouterr().err


def test_run_fails_when_mirror_refused_for_space(setup, monkeypatch):
    tmp_path, settings = setup
    settings["PHOTO_BACKER_UPPER_SECONDARY_DIR"] = str(tmp_path / "secondary")
    monkeypatch.setattr(cli, "require_tool", lambda *a: None)

    def too_big(*a):
        raise ConfigError("the mirror would copy 2100 GiB but the secondary drive has only 900 GiB free")

    monkeypatch.setattr(cli, "check_free_space", too_big)
    with pytest.raises(ConfigError, match="2100 GiB"):
        cli.cmd_run(run_args())
