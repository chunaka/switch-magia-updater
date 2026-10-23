from __future__ import annotations

import stat
import warnings
import zipfile
from pathlib import Path

import pytest

from magia_updater.core.archive import (
    ArchiveError,
    ArchiveValidationError,
    UnsafeArchiveError,
    extract_zip_safely,
)


def make_zip(path: Path, entries: list[tuple[str, bytes]]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return path


def test_extract_files_and_directories(tmp_path: Path) -> None:
    archive = make_zip(
        tmp_path / "update.zip",
        [
            ("atmosphere/package3", b"package3"),
            ("bootloader/update.bin", b"hekate"),
            ("switch/daybreak.nro", b"daybreak"),
        ],
    )

    result = extract_zip_safely(archive, tmp_path / "out")

    assert result.file_count == 3
    assert result.total_size == len(b"package3") + len(b"hekate") + len(b"daybreak")
    assert (tmp_path / "out/atmosphere/package3").read_bytes() == b"package3"
    assert (tmp_path / "out/bootloader/update.bin").read_bytes() == b"hekate"
    assert "atmosphere/package3" in result.files


def test_destination_must_not_exist(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "update.zip", [("file.txt", b"data")])
    destination = tmp_path / "out"
    destination.mkdir()

    with pytest.raises(ArchiveValidationError, match="ya existe"):
        extract_zip_safely(archive, destination)


@pytest.mark.parametrize(
    "name",
    [
        "../evil.txt",
        "../../evil.txt",
        "/evil.txt",
        "..\\evil.txt",
        "C:/evil.txt",
        "a/../../evil.txt",
    ],
)
def test_rejects_unsafe_paths(tmp_path: Path, name: str) -> None:
    archive = make_zip(tmp_path / "update.zip", [(name, b"evil")])
    destination = tmp_path / "out"

    with pytest.raises(UnsafeArchiveError):
        extract_zip_safely(archive, destination)

    assert not destination.exists()
    assert not (tmp_path / "evil.txt").exists()
    assert not list(tmp_path.glob(".magia-extract-*"))


def test_rejects_symlink_entries(tmp_path: Path) -> None:
    archive_path = tmp_path / "update.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "destino")

    with pytest.raises(UnsafeArchiveError, match="enlace"):
        extract_zip_safely(archive_path, tmp_path / "out")


def _mark_entries_encrypted(path: Path) -> None:
    data = bytearray(path.read_bytes())
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        start = 0
        while (index := data.find(signature, start)) != -1:
            data[index + flag_offset] |= 0x1
            start = index + 4
    path.write_bytes(data)


def test_rejects_encrypted_entries(tmp_path: Path) -> None:
    archive_path = make_zip(tmp_path / "update.zip", [("secret.txt", b"secret")])
    _mark_entries_encrypted(archive_path)

    with pytest.raises(UnsafeArchiveError, match="cifrada"):
        extract_zip_safely(archive_path, tmp_path / "out")


def test_rejects_duplicate_entries(tmp_path: Path) -> None:
    archive_path = tmp_path / "update.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("file.txt", b"uno")
            archive.writestr("file.txt", b"dos")

    with pytest.raises(UnsafeArchiveError, match="duplicada"):
        extract_zip_safely(archive_path, tmp_path / "out")


def test_rejects_too_many_entries(tmp_path: Path) -> None:
    archive = make_zip(
        tmp_path / "update.zip",
        [("a.txt", b"a"), ("b.txt", b"b")],
    )

    with pytest.raises(ArchiveValidationError, match="demasiadas"):
        extract_zip_safely(archive, tmp_path / "out", max_entries=1)


def test_rejects_entry_too_large(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "update.zip", [("big.bin", b"12345")])

    with pytest.raises(ArchiveValidationError, match="demasiado grande"):
        extract_zip_safely(archive, tmp_path / "out", max_entry_size=4)


def test_rejects_total_too_large(tmp_path: Path) -> None:
    archive = make_zip(
        tmp_path / "update.zip",
        [("a.bin", b"1234"), ("b.bin", b"5678")],
    )

    with pytest.raises(ArchiveValidationError, match="tamaño total"):
        extract_zip_safely(archive, tmp_path / "out", max_total_size=7)


def test_empty_zip(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "empty.zip", [])

    result = extract_zip_safely(archive, tmp_path / "out")

    assert result.file_count == 0
    assert result.total_size == 0
    assert result.root.is_dir()


def test_bad_zip_is_reported(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    archive.write_bytes(b"no es un zip")

    with pytest.raises(ArchiveError, match="abrir"):
        extract_zip_safely(archive, tmp_path / "out")
