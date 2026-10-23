from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from magia_updater.core.download import sha256_file
from magia_updater.core.export import (
    ExportError,
    ExportFormat,
    ExportKind,
    export_plan,
)
from magia_updater.core.fs import PathSecurityError
from magia_updater.core.models import (
    ComponentId,
    ComponentPlan,
    FileOperation,
    OperationKind,
    UpdatePlan,
)


def _file(root: Path, relative: str, content: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _operation(
    relative: str,
    kind: OperationKind,
    source: Path | None = None,
    *,
    destination_sha256: str | None = None,
    requires_confirmation: bool = False,
) -> FileOperation:
    if source is None:
        return FileOperation(
            relative_path=relative,
            kind=kind,
            destination_sha256=destination_sha256,
            requires_confirmation=requires_confirmation,
        )
    return FileOperation(
        relative_path=relative,
        kind=kind,
        source=source,
        size=source.stat().st_size,
        sha256=sha256_file(source),
        destination_sha256=destination_sha256,
        reason="prueba",
        requires_confirmation=requires_confirmation,
    )


@pytest.fixture
def export_sources(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    root = tmp_path / "sources"
    return (
        _file(root, "create.bin", b"create"),
        _file(root, "replace.bin", b"replace"),
        _file(root, "keep.bin", b"keep"),
        _file(root, "skip.bin", b"skip"),
    )


def _plan(sources: tuple[Path, Path, Path, Path]) -> UpdatePlan:
    create, replace, keep, skip = sources
    return UpdatePlan(
        components=(
            ComponentPlan(
                component=ComponentId.ATMOSPHERE,
                release=None,
                operations=(
                    _operation("atmosphere/package3", OperationKind.CREATE, create),
                    _operation(
                        "switch/daybreak.nro",
                        OperationKind.REPLACE,
                        replace,
                        destination_sha256="a" * 64,
                    ),
                    _operation("bootloader/payloads/fusee.bin", OperationKind.KEEP, keep),
                    _operation("atmosphere/config/settings.ini", OperationKind.SKIP, skip),
                    _operation("payload.bin", OperationKind.KEEP),
                ),
            ),
        )
    )


def test_export_delta_folder_only_includes_changes(
    tmp_path: Path, export_sources: tuple[Path, Path, Path, Path]
) -> None:
    output = tmp_path / "package"

    result = export_plan(
        _plan(export_sources),
        output,
        kind=ExportKind.DELTA,
        format=ExportFormat.FOLDER,
        now=lambda: datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    )

    assert result.path == output
    assert result.file_count == 2
    assert result.total_size == len(b"create") + len(b"replace")
    assert (output / "atmosphere/package3").read_bytes() == b"create"
    assert (output / "switch/daybreak.nro").read_bytes() == b"replace"
    assert not (output / "bootloader/payloads/fusee.bin").exists()
    assert not (output / "atmosphere/config/settings.ini").exists()
    assert not (output / "payload.bin").exists()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["package_type"] == "delta"
    assert manifest["created_at"] == "2026-01-02T03:04:05+00:00"
    assert [entry["path"] for entry in manifest["files"]] == [
        "atmosphere/package3",
        "switch/daybreak.nro",
    ]
    assert "Instrucciones" in (output / "LEEME.txt").read_text(encoding="utf-8")
    assert str(export_sources[0].parent) not in (output / "manifest.json").read_text(
        encoding="utf-8"
    )
    assert str(export_sources[0].parent) not in (output / "LEEME.txt").read_text(encoding="utf-8")
    assert not list(tmp_path.glob(".magia-export-*"))


def test_export_full_folder_includes_keep_with_source(
    tmp_path: Path, export_sources: tuple[Path, Path, Path, Path]
) -> None:
    output = tmp_path / "package"

    result = export_plan(
        _plan(export_sources),
        output,
        kind=ExportKind.FULL,
        format=ExportFormat.FOLDER,
    )

    assert result.file_count == 3
    assert (output / "bootloader/payloads/fusee.bin").read_bytes() == b"keep"
    assert not (output / "atmosphere/config/settings.ini").exists()
    assert not (output / "payload.bin").exists()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["package_type"] == "full"
    kinds = {entry["path"]: entry["kind"] for entry in manifest["files"]}
    assert kinds["bootloader/payloads/fusee.bin"] == "keep"


def test_export_zip_contains_files_manifest_and_readme(
    tmp_path: Path, export_sources: tuple[Path, Path, Path, Path]
) -> None:
    output = tmp_path / "package.zip"

    result = export_plan(
        _plan(export_sources),
        output,
        kind=ExportKind.DELTA,
        format=ExportFormat.ZIP,
    )

    assert result.format == ExportFormat.ZIP
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == [
            "atmosphere/package3",
            "switch/daybreak.nro",
            "manifest.json",
            "LEEME.txt",
        ]
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["file_count"] == 2
        assert archive.read("atmosphere/package3") == b"create"
    assert not list(tmp_path.glob(".magia-export-*"))


def test_export_rejects_existing_destination(
    tmp_path: Path, export_sources: tuple[Path, Path, Path, Path]
) -> None:
    output = tmp_path / "package.zip"
    output.write_bytes(b"existing")

    with pytest.raises(ExportError, match="ya existe"):
        export_plan(_plan(export_sources), output, format=ExportFormat.ZIP)


def test_export_rejects_conflicts(
    tmp_path: Path, export_sources: tuple[Path, Path, Path, Path]
) -> None:
    plan = UpdatePlan(
        components=(
            ComponentPlan(
                component=ComponentId.ATMOSPHERE,
                release=None,
                operations=(
                    _operation(
                        "atmosphere/package3",
                        OperationKind.CONFLICT,
                        export_sources[0],
                        requires_confirmation=True,
                    ),
                ),
            ),
        )
    )

    with pytest.raises(ExportError, match="conflictos"):
        export_plan(plan, tmp_path / "package", format=ExportFormat.FOLDER)


def test_export_rejects_source_hash_mismatch(
    tmp_path: Path, export_sources: tuple[Path, Path, Path, Path]
) -> None:
    operation = _operation("atmosphere/package3", OperationKind.CREATE, export_sources[0])
    operation = FileOperation(
        relative_path=operation.relative_path,
        kind=operation.kind,
        source=operation.source,
        size=operation.size,
        sha256="0" * 64,
    )
    plan = UpdatePlan(
        components=(
            ComponentPlan(
                component=ComponentId.ATMOSPHERE,
                release=None,
                operations=(operation,),
            ),
        )
    )

    with pytest.raises(ExportError, match="hash"):
        export_plan(plan, tmp_path / "package", format=ExportFormat.FOLDER)


def test_export_rejects_duplicate_paths(
    tmp_path: Path, export_sources: tuple[Path, Path, Path, Path]
) -> None:
    plan = UpdatePlan(
        components=(
            ComponentPlan(
                component=ComponentId.ATMOSPHERE,
                release=None,
                operations=(
                    _operation("same.bin", OperationKind.CREATE, export_sources[0]),
                    _operation("same.bin", OperationKind.CREATE, export_sources[1]),
                ),
            ),
        )
    )

    with pytest.raises(ExportError, match="duplicada"):
        export_plan(plan, tmp_path / "package", format=ExportFormat.FOLDER)


def test_export_rejects_empty_plan(
    tmp_path: Path, export_sources: tuple[Path, Path, Path, Path]
) -> None:
    plan = UpdatePlan(
        components=(
            ComponentPlan(
                component=ComponentId.ATMOSPHERE,
                release=None,
                operations=(
                    _operation(
                        "atmosphere/config/settings.ini", OperationKind.SKIP, export_sources[3]
                    ),
                ),
            ),
        )
    )

    with pytest.raises(ExportError, match="no contiene archivos"):
        export_plan(plan, tmp_path / "package", format=ExportFormat.FOLDER)


def test_export_rejects_reserved_names(
    tmp_path: Path, export_sources: tuple[Path, Path, Path, Path]
) -> None:
    plan = UpdatePlan(
        components=(
            ComponentPlan(
                component=ComponentId.ATMOSPHERE,
                release=None,
                operations=(_operation("manifest.json", OperationKind.CREATE, export_sources[0]),),
            ),
        )
    )

    with pytest.raises(ExportError, match="reservada"):
        export_plan(plan, tmp_path / "package", format=ExportFormat.FOLDER)


def test_export_rejects_unsafe_paths(
    tmp_path: Path, export_sources: tuple[Path, Path, Path, Path]
) -> None:
    plan = UpdatePlan(
        components=(
            ComponentPlan(
                component=ComponentId.ATMOSPHERE,
                release=None,
                operations=(_operation("../evil.bin", OperationKind.CREATE, export_sources[0]),),
            ),
        )
    )

    with pytest.raises(PathSecurityError):
        export_plan(plan, tmp_path / "package", format=ExportFormat.FOLDER)
