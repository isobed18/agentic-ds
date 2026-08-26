param(
    [int]$Port = 8077
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$logDirectory = Join-Path $root "data\logs"
$stdoutLog = Join-Path $logDirectory "cloudflared.out.log"
$stderrLog = Join-Path $logDirectory "cloudflared.err.log"
$config = Join-Path $env:USERPROFILE ".cloudflared\config.yml"

$health = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/api/health"
$protected = Invoke-WebRequest `
    -UseBasicParsing `
    "http://127.0.0.1:$Port/api/runs" `
    -SkipHttpErrorCheck
if ($health.StatusCode -ne 200 -or $protected.StatusCode -ne 401) {
    throw "Refusing to start the tunnel: the local health or password-gate check failed."
}

$existing = Get-Process cloudflared -ErrorAction SilentlyContinue
if ($existing) {
    Write-Output "Cloudflare tunnel is already running (PID $($existing.Id -join ', '))."
    exit 0
}
if (-not (Test-Path -LiteralPath $config)) {
    throw "Cloudflare config is missing at $config."
}

$cloudflared = (Get-Command cloudflared -ErrorAction Stop).Source
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$process = Start-Process `
    -FilePath $cloudflared `
    -ArgumentList @("--config", $config, "tunnel", "run") `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru

Start-Sleep -Seconds 2
if ($process.HasExited) {
    $details = if (Test-Path -LiteralPath $stderrLog) {
        (Get-Content -LiteralPath $stderrLog -Tail 20) -join [Environment]::NewLine
    } else {
        "No error log was produced."
    }
    throw "Cloudflare tunnel exited during startup.`n$details"
}

Write-Output "Cloudflare tunnel started (PID $($process.Id))."
Write-Output "stdout: $stdoutLog"
Write-Output "stderr: $stderrLog"
