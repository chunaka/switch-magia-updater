param(
    [string]$Python = "python",
    [switch]$SkipInstall,
    [switch]$SkipChecks
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$Command
    )
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Name falló con código $LASTEXITCODE."
    }
}

try {
    if (-not $SkipInstall) {
        Invoke-Checked "pip upgrade" {
            & $Python -m pip install --upgrade pip
        }
        Invoke-Checked "instalación de dependencias" {
            & $Python -m pip install -e ".[dev,gui,build]"
        }
    }

    if (-not $SkipChecks) {
        Invoke-Checked "pytest" {
            & $Python -m pytest -q
        }
        Invoke-Checked "ruff" {
            & $Python -m ruff check src tests scripts
        }
        Invoke-Checked "mypy" {
            & $Python -m mypy
        }
    }

    Invoke-Checked "PyInstaller (onefile)" {
        & $Python -m PyInstaller --noconfirm --clean packaging/windows/MagiaUpdater.spec
    }
    Invoke-Checked "PyInstaller (portable)" {
        & $Python -m PyInstaller --noconfirm --clean packaging/windows/MagiaUpdaterPortable.spec
    }

    $portable = Join-Path $root "dist/MagiaUpdater-portable"
    if (-not (Test-Path $portable)) {
        throw "No se generó la carpeta portable."
    }
    Copy-Item "packaging/windows/LEEME.txt" (Join-Path $portable "LEEME.txt") -Force
    Compress-Archive -Path (Join-Path $portable "*") `
        -DestinationPath "dist/MagiaUpdater-portable.zip" -Force

    $hashLines = @()
    foreach ($file in @("dist/MagiaUpdater.exe", "dist/MagiaUpdater-portable.zip")) {
        if (-not (Test-Path $file)) {
            throw "No se generó $file."
        }
        $hash = (Get-FileHash $file -Algorithm SHA256).Hash.ToLower()
        $hashLines += "$hash  $(Split-Path -Leaf $file)"
    }
    $hashLines | Set-Content "dist/SHA256SUMS.txt" -Encoding ascii

    Write-Host ""
    Write-Host "Artefactos generados:"
    Get-ChildItem dist | Where-Object { $_.Name -in @(
        "MagiaUpdater.exe",
        "MagiaUpdater-portable.zip",
        "SHA256SUMS.txt"
    ) } | ForEach-Object { Write-Host ("  " + $_.FullName) }
}
finally {
    Pop-Location
}
