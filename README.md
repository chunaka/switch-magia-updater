# Magia Updater

Actualizador seguro de CFW para Nintendo Switch. Gestiona **Atmosphère**, **Hekate** y
**sys-patch** sin tocar configuraciones, sysmodules ni payloads desconocidos.

> La CLI expone `detect`, `check`, `plan`, `apply` y
> `export`. `apply` calcula el plan, pide confirmación, crea un backup, escribe de forma atómica
> y puede revertir la transacción si algo falla. La GUI permite detectar, planificar, aplicar,
> exportar paquetes, gestionar backups y cambiar ajustes.

## Usuarios (sin Python)

1. Descarga la última versión desde
   [Releases](https://github.com/chunaka/switch-magia-updater/releases/latest).
2. Apaga la Switch, saca la SD e insértala en el PC.
3. Abre `MagiaUpdater.exe`. La app detecta la SD y busca actualizaciones automáticamente.
4. Pulsa **Actualizar ahora** y confirma.

No hace falta instalar Python ni ejecutar comandos. El ejecutable no está firmado, así que
SmartScreen o el antivirus pueden mostrar un aviso la primera vez: elige **Más información** >
**Ejecutar de todas formas** solo si lo descargaste desde la página oficial de releases. Si
prefieres una carpeta portable, usa `MagiaUpdater-portable.zip`.

El modo fácil actualiza **solo los componentes ya instalados** (Atmosphère, Hekate y
sys-patch). El botón **Avanzado** permite ver el plan detallado, elegir componentes, exportar
paquetes, gestionar backups y cambiar ajustes. Antes de escribir se crea un backup en
`%LOCALAPPDATA%\MagiaUpdater\backups`.

## Requisitos para desarrollo

- Python 3.12 o superior.
- Windows 10/11 como objetivo principal.
- Para la GUI: PySide6 (`pip install -e ".[gui]"`).

## Instalación de desarrollo

```powershell
cd switch-magia-updater
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e ".[dev,gui]"
```

## Uso

`--sd` es opcional: si se omite, la CLI busca una única SD de Switch conectada. Si hay cero o
varias, muestra un error claro y pide indicar la ruta. La detección es de solo lectura.

```powershell
magia detect
magia check
magia plan

magia detect --sd E:\
magia detect --sd E:\ --json

magia check --sd E:\
magia check --sd E:\ --json
magia check --sd E:\ --offline
magia check --sd E:\ --refresh

magia plan --sd E:\
magia plan --sd E:\ --json
magia plan --sd E:\ --offline
magia plan --sd E:\ --allow-reboot-payload
magia plan --sd E:\ --allow-root-payload
magia plan --sd E:\ --no-payloads

magia apply --sd E:\
magia apply --sd E:\ --yes
magia apply --sd E:\ --offline --yes
magia apply --sd E:\ --mirror-backup
magia apply --sd E:\ --backup-dir D:\MagiaBackups --state-dir D:\MagiaState
magia apply --sd E:\ --keep-backups 5

magia export --sd E:\ --output D:\magia-update.zip
magia export --sd E:\ --kind full --format folder --output D:\magia-update
magia export --sd E:\ --offline --allow-reboot-payload --output D:\magia-update.zip

magia-gui
```

`detect` es **offline**: no descarga nada y no modifica la SD. `check` consulta la última
release estable de cada repositorio, usa ETag/caché y compara versiones y digests SHA-256.
Si no hay red, cae a la caché local.

`plan` descarga los ZIP y payloads que falten, los verifica contra el tamaño y el SHA-256
publicados, extrae los ZIP en la caché y calcula las operaciones. **No escribe en la SD.**
Con `--offline` solo usa releases y assets ya cacheados; si falta algo, falla sin tocar la red.

Por defecto el plan preserva `/atmosphere/reboot_payload.bin` y `/payload.bin`. Para permitir
su actualización hay que usar `--allow-reboot-payload` o `--allow-root-payload`. Los payloads
conocidos (`fusee.bin` y `hekate_ctcaer_<versión>.bin`) se colocan en `/bootloader/payloads`;
`--no-payloads` desactiva esa parte del plan.

`apply` ejecuta el mismo flujo de detección, comprobación, descarga y planificación, muestra el
plan y pide confirmación antes de escribir. En una terminal interactiva se responde `s` para
continuar; en scripts o CI hay que usar `--yes`. `--yes` no sustituye a
`--allow-reboot-payload`/`--allow-root-payload`: esas rutas protegidas siguen necesitando su
flag específico. Si no hay cambios, `apply` termina sin crear backup ni tocar la SD.

`export` calcula el mismo plan pero **no escribe en la SD**: genera un ZIP o una carpeta para
copiar manualmente. `--kind delta` incluye solo archivos nuevos o reemplazados; `--kind full`
incluye además los archivos idénticos que tienen origen. Las rutas preservadas, los `SKIP` y los
payloads protegidos sin origen nunca se incluyen. El paquete contiene los archivos en su ruta
relativa, `manifest.json` con componentes, tamaños y SHA-256, y `LEEME.txt` con instrucciones.
El destino debe ser nuevo; no se sobrescribe un paquete existente. Los conflictos y las fuentes
cuyo hash cambió desde el plan se rechazan.

## GUI

La GUI es opcional y usa PySide6. Instálala con `python -m pip install -e ".[gui]"` y ejecuta
`magia-gui` o `python -m magia_updater.gui`.

Al abrirse busca la SD automáticamente (y cada 2 segundos si se conecta después), detecta los
componentes, marca solo los ya instalados y consulta las últimas versiones. Con la SD lista, el
botón principal hace todo el flujo con una confirmación:

- Si no hay plan, **Buscar actualizaciones** detecta y calcula el plan.
- Si hay cambios, **Actualizar ahora** crea el backup y aplica el plan.
- Si todo está al día, muestra **Todo al día**.

El botón **Avanzado** despliega el plan detallado, el registro, la selección de componentes, la
exportación ZIP/carpeta, la gestión de backups y los ajustes. Las tareas largas se ejecutan en
segundo plano y las descargas muestran progreso. El diálogo final permite abrir la carpeta del
backup.

Los ajustes se guardan en `%LOCALAPPDATA%\MagiaUpdater\settings.json` e incluyen las rutas de
caché, backups y estado, el número de backups a conservar, el espejo de backup en la SD, el modo
offline, la instalación de payloads, los permisos para `/atmosphere/reboot_payload.bin` y
`/payload.bin`, y la última SD usada.

## Compilar el ejecutable

El ejecutable se genera con PyInstaller en Windows:

```powershell
python -m pip install -e ".[dev,gui,build]"
.\scripts\build_exe.ps1
```

El script ejecuta tests, Ruff y Mypy, y genera:

- `dist/MagiaUpdater.exe`: un único archivo, sin consola y con icono.
- `dist/MagiaUpdater-portable.zip`: carpeta portable con el mismo ejecutable y `LEEME.txt`.
- `dist/SHA256SUMS.txt`: hashes de los artefactos.

El icono se puede regenerar con `python scripts/make_icon.py`. El ejecutable no está firmado
digitalmente; es esperable el aviso de SmartScreen. Si se cambia la versión, hay que actualizar
`pyproject.toml`, `src/magia_updater/__init__.py` y `packaging/windows/version_info.txt`.

## Releases automáticas

`.github/workflows/release.yml` compila y publica los artefactos al subir un tag `v*`:

```powershell
git tag v1.0.0
git push origin v1.0.0
```

El workflow corre tests, Ruff y Mypy en Windows con Python 3.13, compila el EXE y el ZIP, genera
los checksums y crea la release con las notas automáticas. `.github/workflows/ci.yml` ejecuta las
mismas comprobaciones en cada push a `main`/`master` y en pull requests.

## Instalación y aplicación

El comando `magia apply` usa la capa `UpdateInstaller` para aplicar un `UpdatePlan` sobre una SD:

- **Preflight**: rechaza rutas con traversal, symlinks, conflictos, destinos que cambiaron desde
  que se creó el plan y fuentes cuyo tamaño o SHA-256 no coinciden. También comprueba el espacio
  libre antes de escribir.
- **Confirmación**: si una escritura afecta a una ruta protegida, exige confirmación explícita.
  Las operaciones `KEEP`/`SKIP` no bloquean la aplicación de cambios seguros.
- **Backup**: guarda los archivos que se van a reemplazar en
  `%LOCALAPPDATA%\MagiaUpdater\backups\<sd_id>\<timestamp>\`, con `manifest.json`, `journal.json`
  y una copia en `files/`. Se conservan los 3 backups más recientes. Opcionalmente se puede
  espejar el backup en `/.magia/backups/<timestamp>` de la SD.
- **Escritura atómica**: cada archivo se copia a un temporal en el mismo directorio, se hace
  `fsync` y se reemplaza con `os.replace`. El journal registra la operación pendiente y las
  completadas para poder recuperar una transacción interrumpida.
- **Rollback**: si falla una escritura, restaura los archivos reemplazados y elimina los creados.
  Si el rollback también falla, deja el journal en `rollback_failed` y reporta los errores.
- **Recuperación**: `UpdateInstaller.recover` detecta journals en estado `applying` y revierte la
  transacción antes de aplicar un plan nuevo.
- **Estado**: guarda la última actualización aplicada en
  `%LOCALAPPDATA%\MagiaUpdater\state\<sd_id>.json`, incluyendo versiones, backup y digest del plan.
- **Identidad de SD**: usa el número de serie del volumen de Windows (`win-<serial>`) o, como
  fallback, el dispositivo y una huella de la ruta (`dev-...`).
- **CLI**: `apply` permite cambiar `--backup-dir`, `--state-dir`, `--mirror-backup` y
  `--keep-backups`; antes de planificar recupera cualquier journal local en estado `applying`.

La caché se guarda por defecto en `%LOCALAPPDATA%\MagiaUpdater\cache`. Incluye las releases,
los assets descargados y los ZIP ya extraídos, identificados por su SHA-256. Para evitar el
límite de la API de GitHub se puede definir `MAGIA_GITHUB_TOKEN` (o `GITHUB_TOKEN`) con un
token de solo lectura; no es obligatorio.

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
  `/atmosphere/hosts`, backups, errores fatales, `/bootloader/hekate_ipl.ini`,
  `/bootloader/ini`, `/bootloader/payloads` ni `/config`.
- En `/switch` se preservan los homebrews del usuario; solo se actualizan las herramientas
  oficiales de Atmosphère (`daybreak.nro`, `haze.nro`, `reboot_to_payload.nro`) y el overlay
  de sys-patch.
- Los payloads desconocidos se preservan y se pide confirmación antes de reemplazarlos.
- La extracción de ZIP rechaza traversal, rutas absolutas o de Windows, enlaces simbólicos,
  entradas cifradas, duplicados y archivos que excedan los límites.
- Las escrituras en la SD usan backup, escritura atómica, journal y rollback; un plan obsoleto se
  rechaza si el destino cambió desde que se calculó.
- La exportación es de solo lectura sobre la SD. El paquete no incluye rutas absolutas locales y
  rechaza traversal, rutas duplicadas, nombres reservados, conflictos y fuentes alteradas.


## Licencia

MIT.
