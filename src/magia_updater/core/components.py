"""Registro de componentes y reglas estáticas de instalación/preservación."""

from __future__ import annotations

from magia_updater.core.models import ComponentDefinition, ComponentId

SYS_PATCH_TID = "420000000000000B"

ATMOSPHERE = ComponentDefinition(
    id=ComponentId.ATMOSPHERE,
    name="Atmosphère",
    repo="Atmosphere-NX/Atmosphere",
    description="CFW base. Incluye hbmenu y las herramientas de sistema.",
    archive_pattern=r"atmosphere-.*\.zip",
    payload_patterns=(r"fusee\.bin",),
    required_files=(
        "atmosphere/package3",
        "atmosphere/stratosphere.romfs",
        "switch/daybreak.nro",
        "switch/reboot_to_payload.nro",
    ),
    install_roots=("atmosphere/", "switch/", "hbmenu.nro"),
    preserve=(
        "atmosphere/config/**",
        "atmosphere/contents/** (excepto 420000000000000B)",
        "atmosphere/hosts/**",
        "atmosphere/automatic_backups/**",
        "atmosphere/fatal_errors/**",
        "bootloader/hekate_ipl.ini",
        "bootloader/ini/**",
        "bootloader/payloads/**",
        "switch/**",
        "config/**",
    ),
)

HEKATE = ComponentDefinition(
    id=ComponentId.HEKATE,
    name="Hekate + Nyx",
    repo="CTCaer/hekate",
    description="Bootloader y payload. Se actualiza solo si está detectado o se selecciona.",
    archive_pattern=r"hekate_ctcaer_.*_Nyx_.*\.zip",
    payload_patterns=(r"hekate_ctcaer_(?!.*__ram8GB).*\.bin",),
    required_files=(
        "bootloader/update.bin",
        "bootloader/sys/nyx.bin",
        "bootloader/sys/libsys_minerva.bso",
    ),
    install_roots=("bootloader/",),
    preserve=(
        "bootloader/hekate_ipl.ini",
        "bootloader/nyx.ini",
        "bootloader/patches.ini",
        "bootloader/ini/**",
        "bootloader/payloads/**",
    ),
    optional=True,
)

SYS_PATCH = ComponentDefinition(
    id=ComponentId.SYS_PATCH,
    name="sys-patch",
    repo="impeeza/sys-patch",
    description="Sysmodule que aplica parches de firma al arrancar.",
    archive_pattern=r"sys-patch-.*\.zip",
    required_files=(
        f"atmosphere/contents/{SYS_PATCH_TID}/exefs.nsp",
        f"atmosphere/contents/{SYS_PATCH_TID}/flags/boot2.flag",
        f"atmosphere/contents/{SYS_PATCH_TID}/toolbox.json",
    ),
    install_roots=(
        "atmosphere/contents/420000000000000B/",
        "switch/.overlays/sys-patch-overlay.ovl",
    ),
    preserve=("config/sys-patch/**",),
    optional=True,
)

HBMENU = ComponentDefinition(
    id=ComponentId.HBMENU,
    name="hbmenu",
    repo="Atmosphere-NX/Atmosphere",
    description="Menú de homebrew incluido en el ZIP de Atmosphère. Solo informativo.",
    required_files=("hbmenu.nro",),
    install_roots=("hbmenu.nro",),
    preserve=(),
    informational=True,
)

COMPONENTS: dict[ComponentId, ComponentDefinition] = {
    component.id: component for component in (ATMOSPHERE, HEKATE, SYS_PATCH, HBMENU)
}

UPDATABLE_COMPONENTS: tuple[ComponentId, ...] = (
    ComponentId.ATMOSPHERE,
    ComponentId.HEKATE,
    ComponentId.SYS_PATCH,
)


def get_component(component: ComponentId) -> ComponentDefinition:
    return COMPONENTS[component]
