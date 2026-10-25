from __future__ import annotations

from pathlib import Path

from magia_updater.core.devices import SdCandidate, find_switch_sds, switch_markers


def _make_switch_sd(root: Path) -> Path:
    (root / "atmosphere").mkdir(parents=True)
    (root / "atmosphere/package3").write_bytes(b"PK31")
    (root / "hbmenu.nro").write_bytes(b"hbmenu")
    return root


def _make_plain_folder(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "readme.txt").write_text("no es una SD", encoding="utf-8")
    return root


def test_switch_markers_detects_known_paths(tmp_path: Path) -> None:
    root = _make_switch_sd(tmp_path / "sd")
    (root / "Nintendo").mkdir()

    assert switch_markers(root) == ("atmosphere/package3", "hbmenu.nro", "Nintendo")


def test_find_switch_sds_filters_plain_folders(tmp_path: Path) -> None:
    sd = _make_switch_sd(tmp_path / "sd")
    plain = _make_plain_folder(tmp_path / "plain")

    candidates = find_switch_sds(roots=[plain, sd])

    assert len(candidates) == 1
    assert candidates[0].root == sd.resolve()
    assert candidates[0].drive_type == "ruta"
    assert "atmosphere/package3" in candidates[0].markers


def test_find_switch_sds_deduplicates_and_sorts(tmp_path: Path) -> None:
    first = _make_switch_sd(tmp_path / "b-sd")
    second = _make_switch_sd(tmp_path / "a-sd")

    candidates = find_switch_sds(roots=[first, second, first])

    assert [candidate.root for candidate in candidates] == [
        second.resolve(),
        first.resolve(),
    ]


def test_find_switch_sds_returns_empty_without_markers(tmp_path: Path) -> None:
    plain = _make_plain_folder(tmp_path / "plain")

    assert find_switch_sds(roots=[plain]) == ()


def test_candidate_display_name_includes_drive_type(tmp_path: Path) -> None:
    candidate = SdCandidate(root=tmp_path / "sd", drive_type="extraíble", markers=())

    assert candidate.display_name.endswith("(extraíble)")
