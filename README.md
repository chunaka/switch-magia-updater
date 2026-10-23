# Magia Updater

Actualizador seguro de CFW para Nintendo Switch. Gestiona **Atmosphère**, **Hekate** y
**sys-patch** sin tocar configuraciones, sysmodules ni payloads desconocidos.

> Estado: **Fases 1 y 2 completadas**. Todavía no escribe en la SD; detecta el estado offline,
> consulta GitHub, selecciona assets y compara versiones/digests. La descarga y la instalación
> llegan en la Fase 3.

## Requisitos

- Python 3.12 o superior.
- Windows 10/11 como objetivo principal.
- Para la GUI: PySide6 (`pip install -e ".[gui]"`).

## Instalación de desarrollo

```powershell
cd switch-magia-updater
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

## Uso

```powershell
magia detect --sd E:\
magia detect --sd E:\ --json

magia check --sd E:\
magia check --sd E:\ --json
magia check --sd E:\ --offline
magia check --sd E:\ --refresh
```

`detect` es **offline**: no descarga nada y no modifica la SD. `check` consulta la última
release estable de cada repositorio, usa ETag/caché y compara versiones y digests SHA-256.
Si no hay red, cae a la caché local.

La caché se guarda por defecto en `%LOCALAPPDATA%\MagiaUpdater\cache`. Para evitar el límite
de la API de GitHub se puede definir `MAGIA_GITHUB_TOKEN` (o `GITHUB_TOKEN`) con un token
de solo lectura; no es obligatorio.

## Qué detecta

| Componente | Fuente | Método |
|---|---|---|
| Atmosphère | `/atmosphere/package3` | Cabecera `PK31` y campos de versión |
| Hekate | `/bootloader/update.bin` | Cadena `vX.Y.Z`; fallback al nombre del payload |
| hbmenu | `/hbmenu.nro` | Cadena `vX.Y.Z` (informativo) |
| sys-patch | `/atmosphere/contents/420000000000000B/exefs.nsp` | Cadena `X.Y.Z.W-hash` |
| Firmware | `/config/sys-patch/log.ini` | Claves `[stats] fw_version` y `ams_version` |

## Seguridad

- No se toca `/emuMMC` ni `/Nintendo`.
- No se modifican `/atmosphere/config`, `/atmosphere/contents` (salvo el TID de sys-patch),
  `/bootloader/hekate_ipl.ini`, `/bootloader/ini`, `/bootloader/payloads` ni `/switch`.
- Los payloads desconocidos se preservan y se preguntan antes de reemplazarlos.
- Las escrituras futuras serán atómicas, con backup y rollback.

## Fases

- [x] Fase 1: estructura, modelos, registro de componentes, detección offline y tests.
- [x] Fase 2: cliente GitHub, caché, selección de assets y comparación de digests.
- [ ] Fase 3: descarga, validación de ZIP, hashes y planner.
- [ ] Fase 4: backup, instalación atómica, rollback y estado.
- [ ] Fase 5: CLI end-to-end y pruebas con SD falsa.
- [ ] Fase 6: GUI PySide6, exportación, backups y ajustes.
- [ ] Fase 7: compatibilidad de firmware, empaquetado y prueba real.

## Contribución

Las convenciones de ramas, commits y verificación están en [CONTRIBUTING.md](CONTRIBUTING.md).

## Licencia

MIT.
