param(
    [string]$Python = "py -3.11",
    [string]$NodeInstallerHint = "https://nodejs.org/",
    [switch]$SkipNodeCheck
)

$ErrorActionPreference = "Stop"

function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$Body
    )
    Write-Host "==> $Name" -ForegroundColor Cyan
    & $Body
}

function Test-Command {
    param([string]$Name)
    $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Invoke-ConfiguredPython {
    param([string[]]$Arguments)
    $pythonParts = $Python -split "\s+"
    if ($pythonParts.Length -eq 1) {
        & $pythonParts[0] @Arguments
    } else {
        $pythonArgs = $pythonParts[1..($pythonParts.Length - 1)]
        & $pythonParts[0] @pythonArgs @Arguments
    }
}

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

Invoke-Step "Checking Python" {
    Invoke-ConfiguredPython @("--version")
}

if (-not $SkipNodeCheck) {
    Invoke-Step "Checking Node.js and npm" {
        if (-not (Test-Command "node")) {
            throw "Node.js was not found. Install Node.js 22+ from $NodeInstallerHint, then rerun this script."
        }
        if (-not (Test-Command "npm")) {
            throw "npm was not found. Install Node.js 22+ from $NodeInstallerHint, then rerun this script."
        }
        node --version
        npm --version
    }
}

Invoke-Step "Creating Python virtual environment" {
    if (-not (Test-Path ".venv\Scripts\python.exe")) {
        Invoke-ConfiguredPython @("-m", "venv", ".venv")
    }
    .\.venv\Scripts\python.exe -m pip install --upgrade pip
}

Invoke-Step "Installing Python package and dependencies" {
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    .\.venv\Scripts\python.exe -m pip install -e .
}

Invoke-Step "Installing AutoResearch Node dependencies" {
    npm --prefix tools\autoresearch install
}

Invoke-Step "Running Windows readiness checks" {
    .\.venv\Scripts\python.exe -m bci_autoresearch.product_shell.cli doctor --json
    .\.venv\Scripts\python.exe -m bci_autoresearch.product_shell.cli windows doctor
}

Write-Host ""
Write-Host "AutoBci Windows setup completed." -ForegroundColor Green
Write-Host "Start with: .\.venv\Scripts\autobci.exe"
