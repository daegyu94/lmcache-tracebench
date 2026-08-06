from pathlib import Path

import pytest

from recorder.live import _format_duration, reset_l2_storage


def test_format_duration():
    assert _format_duration(0) == "00:00:00"
    assert _format_duration(3661.9) == "01:01:01"


def test_reset_l2_storage_removes_existing_contents(tmp_path: Path):
    l2_path = tmp_path / "storage" / "run"
    l2_path.mkdir(parents=True)
    (l2_path / "old-cache-entry").write_text("old", encoding="utf-8")

    result = reset_l2_storage(l2_path)

    assert result == l2_path.resolve()
    assert result.is_dir()
    assert list(result.iterdir()) == []


def test_reset_l2_storage_rejects_relative_path():
    with pytest.raises(ValueError, match="absolute"):
        reset_l2_storage("relative/cache")


def test_reset_l2_storage_rejects_protected_ancestor(tmp_path: Path):
    protected = tmp_path / "storage" / "run"
    with pytest.raises(ValueError, match="protected"):
        reset_l2_storage(tmp_path, protected_paths=(protected,))
