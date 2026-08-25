param(
    [int]$Port = 8077
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = (Resolve-Path (Join-Path $root ".venv\Scripts\python.exe")).Path
$launcher = (Resolve-Path (Join-Path $root "scripts\serve_public.py")).Path
$logDirectory = Join-Path $root "data\logs"
$stdoutLog = Join-Path $logDirectory "public-server.out.log"
$stderrLog = Join-Path $logDirectory "public-server.err.log"

$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Output "Agentic DS is already listening on 127.0.0.1:$Port (PID $($existing.OwningProcess))."
    exit 0
}

New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

$process = Start-Process `
    -FilePath $python `
    -ArgumentList @($launcher, "--port", $Port) `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru

$listener = $null
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Milliseconds 500
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        break
    }
    if ($process.HasExited) {
        break
    }
}

if (-not $listener) {
    $details = if (Test-Path $stderrLog) {
        (Get-Content $stderrLog -Tail 20) -join [Environment]::NewLine
    } else {
        "No error log was produced."
    }
    throw "Agentic DS did not start on port $Port.`n$details"
}

Write-Output "Agentic DS started on 127.0.0.1:$Port (PID $($listener.OwningProcess))."
Write-Output "stdout: $stdoutLog"
Write-Output "stderr: $stderrLog"
