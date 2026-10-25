"""CLI de Magia Updater: detección, comprobación, planificación y aplicación."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from magia_updater import __version__
from magia_updater.core.cache import CacheStore
from magia_updater.core.check import ReleaseService
from magia_updater.core.components import UPDATABLE_COMPONENTS
from magia_updater.core.detect import component_names, detect_sd
from magia_updater.core.devices import find_switch_sds
from magia_updater.core.errors import MagiaError
from magia_updater.core.export import ExportFormat, ExportKind, export_plan
from magia_updater.core.github import GitHubClient
from magia_updater.core.installer import ApplyOptions, UpdateInstaller
from magia_updater.core.models import (
    CheckStatus,
    ComponentCheck,
    ComponentId,
    InstalledComponent,
    OperationKind,
    SdSnapshot,
    UpdatePlan,
    component_check_to_dict,
    snapshot_to_dict,
    update_plan_to_dict,
)
from magia_updater.core.workflow import build_update_plan

STATUS_LABELS = {
    CheckStatus.UPDATE_AVAILABLE: "actualización disponible",
    CheckStatus.UP_TO_DATE: "al día",
    CheckStatus.NOT_INSTALLED: "no instalado",
    CheckStatus.INSTALLED_NEWER: "instalado más nuevo",
    CheckStatus.UNKNOWN: "sin datos",
}


def _format_installed(installed: InstalledComponent) -> str:
    if not installed.present:
        return "no detectado"
    if installed.version is None:
        return "presente (versión desconocida)"
    text = str(installed.version)
    if installed.evidence:
        text = f"{text} ({installed.evidence})"
    return text


def _print_snapshot(snapshot: SdSnapshot) -> None:
    names = component_names()
    print(f"SD: {snapshot.root}")
    print(f"SD de Switch: {'sí' if snapshot.is_switch_sd else 'no'}")
    print(f"CFW detectado: {'sí' if snapshot.has_cfw else 'no'}")
    print()

    for component_id in (
        ComponentId.ATMOSPHERE,
        ComponentId.HEKATE,
        ComponentId.SYS_PATCH,
        ComponentId.HBMENU,
    ):
        installed = snapshot.components[component_id]
        print(f"{names[component_id]}: {_format_installed(installed)}")
        if installed.supported_firmware is not None:
            print(f"  Firmware soportado por el paquete: {installed.supported_firmware}")

    firmware = snapshot.firmware
    if firmware.version is None:
        print("Firmware: desconocido")
    else:
        source = f" ({firmware.source})" if firmware.source else ""
        print(f"Firmware: {firmware.version}{source}")
    if firmware.ams_version is not None:
        print(f"AMS en el último arranque: {firmware.ams_version}")

    print()
    print(f"EmuMMC: {'sí' if snapshot.emummc_present else 'no'}")
    print(f"/atmosphere/config: {'sí' if snapshot.atmosphere_config_present else 'no'}")
    print(f"hekate_ipl.ini: {'sí' if snapshot.hekate_config_present else 'no'}")
    print(f"Payload raíz: {'sí' if snapshot.root_payload else 'no'}")
    print(f"reboot_payload.bin: {'sí' if snapshot.reboot_payload else 'no'}")
    print(f"Payloads en /bootloader/payloads: {len(snapshot.payloads)}")

    if snapshot.warnings:
        print()
        print("Advertencias:")
        for warning in snapshot.warnings:
            print(f"  - {warning}")

    if snapshot.errors:
        print()
        print("Errores:")
        for error in snapshot.errors:
            print(f"  - {error}")


def _print_checks(checks: Sequence[ComponentCheck]) -> None:
    names = component_names()
    for check in checks:
        installed = str(check.installed) if check.installed else "no instalado"
        latest = str(check.latest) if check.latest else "desconocida"
        cache = " [caché]" if check.from_cache else ""
        print(
            f"{names[check.component]}: {installed} -> {latest}"
            f" ({STATUS_LABELS[check.status]}){cache}"
        )
        if check.error:
            print(f"  Error: {check.error}")
        for warning in check.warnings:
            print(f"  Aviso: {warning}")


def _print_plan(plan: UpdatePlan) -> None:
    names = component_names()
    if not plan.components:
        print("No hay actualizaciones para planificar.")
        return

    labels = {
        OperationKind.CREATE: "crear",
        OperationKind.REPLACE: "reemplazar",
        OperationKind.KEEP: "mantener",
        OperationKind.SKIP: "omitir",
        OperationKind.CONFLICT: "conflicto",
    }
    for component_plan in plan.components:
        release = (
            component_plan.release.tag if component_plan.release is not None else "desconocida"
        )
        print(f"{names[component_plan.component]} -> {release}")
        for operation in component_plan.operations:
            marker = " [confirmar]" if operation.requires_confirmation else ""
            reason = f" ({operation.reason})" if operation.reason else ""
            print(f"  {labels[operation.kind]:<12} {operation.relative_path}{marker}{reason}")
        for warning in component_plan.warnings:
            print(f"  Aviso: {warning}")
        print()

    if plan.warnings:
        print("Advertencias del plan:")
        for warning in plan.warnings:
            print(f"  - {warning}")


def _resolve_sd(raw: str | None) -> Path:
    """Usa ``--sd`` o busca una única SD conectada."""
    if raw is not None:
        return Path(raw)
    candidates = find_switch_sds()
    if not candidates:
        raise MagiaError(
            "No se detectó ninguna SD de Switch. Conéctala o indica la ruta con --sd."
        )
    if len(candidates) > 1:
        options = ", ".join(str(candidate.root) for candidate in candidates)
        raise MagiaError(
            f"Se detectaron varias SD de Switch: {options}. Usa --sd para elegir una."
        )
    return candidates[0].root


def _parse_components(raw: str) -> tuple[ComponentId, ...]:
    valid = {component.value: component for component in UPDATABLE_COMPONENTS}
    result: list[ComponentId] = []
    for name in raw.split(","):
        key = name.strip().lower()
        if not key:
            continue
        component = valid.get(key)
        if component is None:
            allowed = ", ".join(valid)
            raise ValueError(f"Componente inválido: {key!r}. Válidos: {allowed}")
        if component not in result:
            result.append(component)
    if not result:
        raise ValueError("No se indicó ningún componente.")
    return tuple(result)


def _ask_confirmation() -> bool | None:
    if not sys.stdin.isatty():
        return None
    try:
        answer = input("¿Aplicar estos cambios? [s/N] ")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer.strip().lower() in {"s", "si", "sí", "y", "yes"}


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("debe ser un entero mayor o igual a 1")
    return number


def _add_update_arguments(
    parser: argparse.ArgumentParser,
    *,
    include_json: bool,
) -> None:
    parser.add_argument(
        "--sd",
        default=None,
        help="Ruta raíz de la SD (ej. E:\\). Si se omite, se detecta automáticamente.",
    )
    parser.add_argument(
        "--components",
        default="atmosphere,hekate,sys-patch",
        help="Lista separada por comas (default: atmosphere,hekate,sys-patch).",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="No consulta GitHub ni descarga; usa solo la caché local.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ignora la caché fresca y fuerza la consulta a GitHub.",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="Directorio de caché (default: %%LOCALAPPDATA%%\\MagiaUpdater\\cache).",
    )
    parser.add_argument(
        "--allow-reboot-payload",
        action="store_true",
        help="Permite actualizar /atmosphere/reboot_payload.bin.",
    )
    parser.add_argument(
        "--allow-root-payload",
        action="store_true",
        help="Permite crear o actualizar /payload.bin con un payload conocido.",
    )
    parser.add_argument(
        "--no-payloads",
        action="store_true",
        help="No instala fusee.bin ni hekate_ctcaer_*.bin en /bootloader/payloads.",
    )
    if include_json:
        parser.add_argument("--json", action="store_true", help="Salida JSON para scripts.")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="magia",
        description="Actualizador seguro de CFW para Nintendo Switch.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    detect_parser = subparsers.add_parser(
        "detect",
        help="Detecta versiones instaladas sin modificar la SD.",
    )
    detect_parser.add_argument(
        "--sd",
        default=None,
        help="Ruta raíz de la SD (ej. E:\\). Si se omite, se detecta automáticamente.",
    )
    detect_parser.add_argument("--json", action="store_true", help="Salida JSON para scripts.")

    check_parser = subparsers.add_parser(
        "check",
        help="Compara lo instalado con las últimas releases de GitHub.",
    )
    check_parser.add_argument(
        "--sd",
        default=None,
        help="Ruta raíz de la SD (ej. E:\\). Si se omite, se detecta automáticamente.",
    )
    check_parser.add_argument(
        "--components",
        default="atmosphere,hekate,sys-patch",
        help="Lista separada por comas (default: atmosphere,hekate,sys-patch).",
    )
    check_parser.add_argument(
        "--offline",
        action="store_true",
        help="No consulta GitHub; usa solo la caché local.",
    )
    check_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ignora la caché fresca y fuerza la consulta a GitHub.",
    )
    check_parser.add_argument(
        "--cache-dir",
        default=None,
        help="Directorio de caché (default: %%LOCALAPPDATA%%\\MagiaUpdater\\cache).",
    )
    check_parser.add_argument("--json", action="store_true", help="Salida JSON para scripts.")

    plan_parser = subparsers.add_parser(
        "plan",
        help="Descarga lo necesario y calcula un plan sin escribir en la SD.",
    )
    _add_update_arguments(plan_parser, include_json=True)

    apply_parser = subparsers.add_parser(
        "apply",
        help="Aplica las actualizaciones con backup, rollback y confirmación.",
    )
    _add_update_arguments(apply_parser, include_json=False)
    apply_parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Aplica sin pedir confirmación interactiva.",
    )
    apply_parser.add_argument(
        "--backup-dir",
        default=None,
        help="Directorio de backups (default: %%LOCALAPPDATA%%\\MagiaUpdater\\backups).",
    )
    apply_parser.add_argument(
        "--state-dir",
        default=None,
        help="Directorio de estado (default: %%LOCALAPPDATA%%\\MagiaUpdater\\state).",
    )
    apply_parser.add_argument(
        "--mirror-backup",
        action="store_true",
        help="Copia el backup también a /.magia/backups en la SD.",
    )
    apply_parser.add_argument(
        "--keep-backups",
        type=_positive_int,
        default=3,
        help="Número de backups locales a conservar (default: 3).",
    )

    export_parser = subparsers.add_parser(
        "export",
        help="Crea un paquete ZIP o una carpeta para actualizar la SD manualmente.",
    )
    _add_update_arguments(export_parser, include_json=False)
    export_parser.add_argument(
        "--format",
        choices=[item.value for item in ExportFormat],
        default=ExportFormat.ZIP.value,
        help="Formato del paquete (default: zip).",
    )
    export_parser.add_argument(
        "--kind",
        choices=[item.value for item in ExportKind],
        default=ExportKind.DELTA.value,
        help="Paquete delta (solo cambios) o full (también archivos idénticos).",
    )
    export_parser.add_argument(
        "--output",
        required=True,
        help="Ruta del ZIP o de la carpeta a crear. No debe existir.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "detect":
        try:
            snapshot = detect_sd(_resolve_sd(args.sd))
        except MagiaError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

        if args.json:
            print(json.dumps(snapshot_to_dict(snapshot), ensure_ascii=False, indent=2))
        else:
            _print_snapshot(snapshot)
        return 0 if snapshot.is_switch_sd else 1

    if args.command == "check":
        try:
            snapshot = detect_sd(_resolve_sd(args.sd))
            components = _parse_components(args.components)
        except (MagiaError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        if not snapshot.is_switch_sd:
            print("Error: la carpeta no parece una SD de Switch.", file=sys.stderr)
            return 2

        token = os.environ.get("MAGIA_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
        service = ReleaseService(
            GitHubClient(token=token),
            CacheStore(args.cache_dir),
        )
        checks = service.check_all(
            snapshot,
            components=components,
            offline=args.offline,
            force=args.refresh,
        )

        if args.json:
            payload = {
                "root": str(snapshot.root),
                "checks": {
                    check.component.value: component_check_to_dict(check) for check in checks
                },
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            _print_checks(checks)
        return 1 if any(check.status == CheckStatus.UNKNOWN for check in checks) else 0

    if args.command == "plan":
        try:
            snapshot = detect_sd(_resolve_sd(args.sd))
            components = _parse_components(args.components)
        except (MagiaError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        if not snapshot.is_switch_sd:
            print("Error: la carpeta no parece una SD de Switch.", file=sys.stderr)
            return 2

        try:
            checks, plan = build_update_plan(
                snapshot,
                components,
                cache=CacheStore(args.cache_dir),
                offline=args.offline,
                refresh=args.refresh,
                no_payloads=args.no_payloads,
                allow_reboot_payload=args.allow_reboot_payload,
                allow_root_payload=args.allow_root_payload,
            )
        except MagiaError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

        if args.json:
            payload = {
                "root": str(snapshot.root),
                "checks": {
                    check.component.value: component_check_to_dict(check) for check in checks
                },
                "plan": update_plan_to_dict(plan),
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            _print_checks(checks)
            print()
            _print_plan(plan)
        return 1 if any(check.status == CheckStatus.UNKNOWN for check in checks) else 0

    if args.command == "apply":
        try:
            snapshot = detect_sd(_resolve_sd(args.sd))
            components = _parse_components(args.components)
        except (MagiaError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        if not snapshot.is_switch_sd:
            print("Error: la carpeta no parece una SD de Switch.", file=sys.stderr)
            return 2

        options = ApplyOptions(
            mirror_backup_to_sd=args.mirror_backup,
            backup_root=Path(args.backup_dir) if args.backup_dir else None,
            state_root=Path(args.state_dir) if args.state_dir else None,
            keep_backups=args.keep_backups,
        )
        installer = UpdateInstaller()
        try:
            recovered = installer.recover(snapshot, options=options)
            if recovered:
                print("Transacciones incompletas recuperadas:")
                for backup_dir in recovered:
                    print(f"  - {backup_dir}")
                snapshot = detect_sd(_resolve_sd(args.sd))
        except (MagiaError, OSError) as exc:
            print(f"Error al recuperar una transacción incompleta: {exc}", file=sys.stderr)
            return 2

        try:
            checks, plan = build_update_plan(
                snapshot,
                components,
                cache=CacheStore(args.cache_dir),
                offline=args.offline,
                refresh=args.refresh,
                no_payloads=args.no_payloads,
                allow_reboot_payload=args.allow_reboot_payload,
                allow_root_payload=args.allow_root_payload,
            )
        except MagiaError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

        _print_checks(checks)
        print()
        _print_plan(plan)

        conflicts = plan.conflicts
        if conflicts:
            paths = ", ".join(operation.relative_path for operation in conflicts)
            print(f"Error: el plan contiene conflictos: {paths}", file=sys.stderr)
            return 2
        writes = plan.changes
        if not writes:
            print("No hay cambios que aplicar.")
            return 0

        if not args.yes:
            confirmation = _ask_confirmation()
            if confirmation is None:
                print(
                    "Error: se requiere --yes para aplicar sin una terminal interactiva.",
                    file=sys.stderr,
                )
                return 2
            if not confirmation:
                print("Aplicación cancelada.")
                return 1

        try:
            result = installer.apply(snapshot, plan, options=options)
        except (MagiaError, OSError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

        print("Actualización aplicada.")
        print(f"Backup: {result.backup_dir}")
        if result.state_path is not None:
            print(f"Estado: {result.state_path}")
        print(f"Operaciones aplicadas: {len(result.applied)}")
        for warning in result.warnings:
            print(f"Aviso: {warning}")
        return 0

    if args.command == "export":
        try:
            snapshot = detect_sd(_resolve_sd(args.sd))
            components = _parse_components(args.components)
        except (MagiaError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        if not snapshot.is_switch_sd:
            print("Error: la carpeta no parece una SD de Switch.", file=sys.stderr)
            return 2

        output = Path(args.output)
        if output.exists():
            print(f"Error: el destino ya existe: {output}", file=sys.stderr)
            return 2

        try:
            checks, plan = build_update_plan(
                snapshot,
                components,
                cache=CacheStore(args.cache_dir),
                offline=args.offline,
                refresh=args.refresh,
                no_payloads=args.no_payloads,
                allow_reboot_payload=args.allow_reboot_payload,
                allow_root_payload=args.allow_root_payload,
            )
        except MagiaError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

        _print_checks(checks)
        print()
        _print_plan(plan)

        try:
            exported = export_plan(
                plan,
                output,
                kind=ExportKind(args.kind),
                format=ExportFormat(args.format),
            )
        except (MagiaError, OSError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

        print(f"Paquete exportado: {exported.path}")
        print(f"Tipo: {exported.kind.value}")
        print(f"Archivos: {exported.file_count}")
        print(f"Tamaño: {exported.total_size} bytes")
        return 0

    parser.error(f"Comando no implementado: {args.command}")
    return 2
