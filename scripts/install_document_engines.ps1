param(
    [string]$Python = "3.12"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$workerRoot = Join-Path $repoRoot ".document-envs"

uv pip install --python (Join-Path $repoRoot ".venv\Scripts\python.exe") `
    "docling>=2.117,<3" "unstructured[pdf]>=0.18,<1"

$markerRoot = Join-Path $workerRoot "marker"
uv venv --python $Python $markerRoot
uv pip install --python (Join-Path $markerRoot "Scripts\python.exe") "marker-pdf>=1.10,<2"

$minerURoot = Join-Path $workerRoot "mineru"
uv venv --python $Python $minerURoot
uv pip install --python (Join-Path $minerURoot "Scripts\python.exe") "mineru[all]>=3.3,<4"

Write-Host "Installed Docling and Unstructured in the application venv."
Write-Host "Installed Marker and MinerU in isolated worker venvs under $workerRoot."
