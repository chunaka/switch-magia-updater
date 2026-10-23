# Convenciones de desarrollo

Este documento define cómo se organizan las ramas, los commits y la revisión del proyecto.

## Commits

Usamos [Conventional Commits 1.0.0](https://www.conventionalcommits.org/).

Formato:

```text
<tipo>(<alcance>): <descripción imperativa>
```

En este proyecto el mensaje se compone **solo del asunto**: sin cuerpo y sin footer, salvo que
exista una razón excepcional (por ejemplo, un `BREAKING CHANGE`). El contexto adicional va en el
issue, en la descripción del PR o en la documentación.

Reglas:

- Asunto en inglés.
- Imperativo, en minúscula y sin punto final.
- Máximo 72 caracteres.
- Un commit representa una unidad lógica de trabajo.
- No mezclar feature, refactor y tests no relacionados en el mismo commit.
- No usar mensajes como `update`, `changes`, `wip`, `final` o `arreglos`.

### Tipos

| Tipo | Uso |
|---|---|
| `feat` | Nueva funcionalidad |
| `fix` | Corrección de un bug |
| `test` | Tests nuevos o ajustados |
| `refactor` | Cambio interno sin modificar el comportamiento |
| `docs` | Documentación |
| `chore` | Configuración, tooling y tareas auxiliares |
| `build` | Empaquetado y dependencias |
| `perf` | Mejora de rendimiento |

### Alcances

```text
core, detect, components, github, cache, assets, check, cli,
download, zip, planner, installer, export, settings, backup, gui, docs
```

### Ejemplos

```text
feat(detect): add offline SD version detection
feat(github): cache latest releases with ETag
fix(zip): reject paths outside the SD root
test(planner): cover payload preservation
docs: document commit conventions
chore: initialize Python project and tooling
```

### Cambios incompatibles

Si un cambio rompe compatibilidad, usar `!` después del tipo o del alcance:

```text
feat(cli)!: replace the check output format
```

## Ramas

`main` debe quedar siempre en un estado probado. El trabajo se hace en ramas cortas por fase o por
tarea:

```text
feat/phase-3-download-planner
fix/zip-slip
docs/commit-conventions
chore/tooling
```

Flujo recomendado:

1. Crear la rama desde `main`.
2. Hacer commits pequeños y lógicos.
3. Ejecutar tests, lint y type-check.
4. Reintegrar a `main` con fast-forward o rebase.
5. Borrar la rama.

No se reescribe historial ya publicado. Si el proyecto se publica en un remoto, los cambios de
historial solo se hacen antes del primer push o en una rama de backup explícita.

## Frecuencia

Se espera más de un commit por fase. Como referencia, una fase mediana debería tener entre 5 y 10
commits: uno por funcionalidad, uno por corrección y uno por documentación. Los tests de una
funcionalidad se incluyen en el mismo commit o en un commit inmediatamente posterior.

## Verificación antes de commitear

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .
.\.venv\Scripts\mypy.exe src/magia_updater
```

Además:

- No incluir tokens, rutas personales ni archivos de la SD.
- No incluir `.venv`, cachés ni artefactos de build.
- No escribir en una SD real durante el desarrollo; usar una SD falsa o una copia.

## Versiones

El proyecto usa SemVer. No se etiqueta cada fase. La primera etiqueta será `v0.1.0-alpha.1` cuando
exista un flujo usable de punta a punta (descarga, plan y aplicación segura).
