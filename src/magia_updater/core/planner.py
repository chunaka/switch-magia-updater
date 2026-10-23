"""Planner puro: convierte un componente preparado en operaciones seguras."""

from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path

from magia_updater.core.components import SYS_PATCH_TID
from magia_updater.core.download import sha256_file
from magia_updater.core.errors import MagiaError
from magia_updater.core.models import (
    ComponentId,
    ComponentPlan,
    FileOperation,
    OperationKind,
    PayloadPolicy,
    SdSnapshot,
    UpdatePlan,
)
from magia_updater.core.prepare import PreparedComponent

GLOBAL_PRESERVE = (
    "emuMMC/**",
    "Nintendo/**",
    "atmosphere/config/**",
    "atmosphere/hosts/**",
    "atmosphere/automatic_backups/**",
    "atmosphere/fatal_errors/**",
    "atmosphere/contents/**",
    "bootloader/hekate_ipl.ini",
    "bootloader/nyx.ini",
    "bootloader/patches.ini",
    "bootloader/ini/**",
    "bootloader/payloads/**",
    "config/**",
    "switch/**",
)

MANAGED_SWITCH_FILES: dict[ComponentId, frozenset[str]] = {
    ComponentId.ATMOSPHERE: frozenset(
        {
            "switch/daybreak.nro",
            "switch/haze.nro",
            "switch/reboot_to_payload.nro",
        }
    ),
    ComponentId.SYS_PATCH: frozenset({"switch/.overlays/sys-patch-overlay.ovl"}),
}


class PlanError(MagiaError):
    """No se pudo construir un plan de actualización."""


def is_preserved(relative_path: str, component: ComponentId) -> bool:
    if not any(fnmatchcase(relative_path, pattern) for pattern in GLOBAL_PRESERVE):
        return False
    if (
        relative_path.startswith(f"atmosphere/contents/{SYS_PATCH_TID}/")
        and component == ComponentId.SYS_PATCH
    ):
        return False
    return relative_path not in MANAGED_SWITCH_FILES.get(component, frozenset())


def _operation_for_source(
    sd_root: Path,
    relative_path: str,
    source: Path,
    *,
    reason: str,
) -> FileOperation:
    destination = sd_root / relative_path
    source_hash = sha256_file(source)
    size = source.stat().st_size
    if destination.exists() and not destination.is_file():
        return FileOperation(
            relative_path=relative_path,
            kind=OperationKind.CONFLICT,
            source=source,
            size=size,
            sha256=source_hash,
            reason="el destino existe y no es un archivo",
            requires_confirmation=True,
        )
    if destination.is_file():
        if sha256_file(destination) == source_hash:
            return FileOperation(
                relative_path=relative_path,
                kind=OperationKind.KEEP,
                source=source,
                size=size,
                sha256=source_hash,
                reason="idéntico",
            )
        return FileOperation(
            relative_path=relative_path,
            kind=OperationKind.REPLACE,
            source=source,
            size=size,
            sha256=source_hash,
            reason=reason,
        )
    return FileOperation(
        relative_path=relative_path,
        kind=OperationKind.CREATE,
        source=source,
        size=size,
        sha256=source_hash,
        reason=reason,
    )


def _is_root_payload_file(relative_path: str) -> bool:
    if "/" in relative_path:
        return False
    return (
        relative_path == "payload.bin"
        or relative_path == "fusee.bin"
        or (relative_path.startswith("hekate_ctcaer_") and relative_path.endswith(".bin"))
    )


def _root_payload_source(prepared: PreparedComponent) -> Path | None:
    for name, path in prepared.payloads.items():
        if name == "fusee.bin":
            return path
        if name.startswith("hekate_ctcaer_") and "__ram8GB" not in name:
            return path
    return None


def plan_component(
    snapshot: SdSnapshot,
    prepared: PreparedComponent,
    policy: PayloadPolicy | None = None,
) -> ComponentPlan:
    policy = policy if policy is not None else PayloadPolicy()
    check = prepared.check
    if check.release is None:
        raise PlanError(f"No hay release para {check.component}.")
    if not prepared.archive_root.is_dir():
        raise PlanError(f"No existe el árbol extraído de {check.component}.")

    operations: dict[str, FileOperation] = {}
    warnings: list[str] = []
    reboot_seen = False

    for source in sorted(prepared.archive_root.rglob("*")):
        if not source.is_file():
            continue
        relative = source.relative_to(prepared.archive_root).as_posix()

        if _is_root_payload_file(relative):
            operations[relative] = FileOperation(
                relative_path=relative,
                kind=OperationKind.SKIP,
                size=source.stat().st_size,
                reason="payload conocido; se instala en /bootloader/payloads",
            )
            continue

        if relative == "atmosphere/reboot_payload.bin":
            reboot_seen = True
            if not policy.update_reboot_payload:
                destination = snapshot.root / relative
                if destination.is_file():
                    operations[relative] = FileOperation(
                        relative_path=relative,
                        kind=OperationKind.KEEP,
                        size=destination.stat().st_size,
                        reason="payload existente; confirmar para actualizar",
                        requires_confirmation=True,
                    )
                else:
                    operations[relative] = FileOperation(
                        relative_path=relative,
                        kind=OperationKind.SKIP,
                        size=source.stat().st_size,
                        reason="no se crea sin confirmación",
                        requires_confirmation=True,
                    )
                warnings.append(
                    "Se preservó /atmosphere/reboot_payload.bin; confirmá para actualizarlo."
                )
                continue

        if is_preserved(relative, check.component):
            operations[relative] = FileOperation(
                relative_path=relative,
                kind=OperationKind.SKIP,
                size=source.stat().st_size,
                reason="ruta preservada por política",
            )
            continue

        operations[relative] = _operation_for_source(
            snapshot.root,
            relative,
            source,
            reason="archivo de la release",
        )

    if policy.install_payloads:
        for name, payload_path in sorted(prepared.payloads.items()):
            safe_name = Path(name).name
            relative = f"bootloader/payloads/{safe_name}"
            operations[relative] = _operation_for_source(
                snapshot.root,
                relative,
                payload_path,
                reason="payload conocido",
            )
    elif prepared.payloads:
        warnings.append("No se instalarán payloads por política.")

    root_source = _root_payload_source(prepared)
    if policy.update_root_payload:
        if root_source is None:
            warnings.append("No hay un payload adecuado para /payload.bin.")
        else:
            operations["payload.bin"] = _operation_for_source(
                snapshot.root,
                "payload.bin",
                root_source,
                reason="payload raíz solicitado",
            )
    elif snapshot.root_payload is not None:
        operations["payload.bin"] = FileOperation(
            relative_path="payload.bin",
            kind=OperationKind.KEEP,
            size=snapshot.root_payload.stat().st_size,
            reason="payload raíz preservado; confirmar para actualizar",
            requires_confirmation=True,
        )
        warnings.append("Se preservó /payload.bin; confirmá para actualizarlo.")

    if (
        not reboot_seen
        and snapshot.reboot_payload is not None
        and check.component == ComponentId.ATMOSPHERE
    ):
        operations["atmosphere/reboot_payload.bin"] = FileOperation(
            relative_path="atmosphere/reboot_payload.bin",
            kind=OperationKind.KEEP,
            size=snapshot.reboot_payload.stat().st_size,
            reason="payload existente; confirmar para actualizar",
            requires_confirmation=True,
        )
        warnings.append("Se preservó /atmosphere/reboot_payload.bin; confirmá para actualizarlo.")

    return ComponentPlan(
        component=check.component,
        release=check.release,
        operations=tuple(operations[key] for key in sorted(operations)),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def plan_update(
    snapshot: SdSnapshot,
    prepared: tuple[PreparedComponent, ...],
    policy: PayloadPolicy | None = None,
) -> UpdatePlan:
    components = tuple(plan_component(snapshot, item, policy) for item in prepared)
    warnings = tuple(warning for component in components for warning in component.warnings)
    return UpdatePlan(components=components, warnings=tuple(dict.fromkeys(warnings)))
