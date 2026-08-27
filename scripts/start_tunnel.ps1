param(
    [int]$Port = 8077
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$logDirectory = Join-Path $root "data\logs"
$stdoutLog = Join-Path $logDirectory "cloudflared.out.log"
$stderrLog = Join-Path $logDirectory "cloudflared.err.log"
$config = Join-Path $env:USERPROFILE ".cloudflared\config.yml"

# Status of a request without throwing on a non-2xx. -SkipHttpErrorCheck would
# say this in one word, but it is PowerShell 7 only, and Windows PowerShell 5.1
# is what is actually installed here -- where a 401 is a terminating error and
# the status has to be read back off the exception. The preflight below depends
# on seeing that 401, so this cannot simply be dropped.
function Get-StatusCode {
    param([string]$Uri)
    try {
        return (Invoke-WebRequest -UseBasicParsing -Uri $Uri).StatusCode
    } catch [System.Net.WebException] {
        if ($_.Exception.Response) {
            return [int]$_.Exception.Response.StatusCode
        }
        throw
    }
}

# Refuse to expose an origin that is not already gated. A 200 here would mean
# the password check is off, and the tunnel would publish it to the internet.
$healthStatus = Get-StatusCode "http://127.0.0.1:$Port/api/health"
$protectedStatus = Get-StatusCode "http://127.0.0.1:$Port/api/runs"
if ($healthStatus -ne 200 -or $protectedStatus -ne 401) {
    throw "Refusing to start the tunnel: local health was $healthStatus (want 200) and the password gate returned $protectedStatus (want 401)."
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
