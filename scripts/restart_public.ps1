param(
    [int]$Port = 8077,
    [int]$StopTimeoutSeconds = 20
)

# Stop whatever is serving, then start again.
#
# `start_public.ps1` deliberately exits early when the port is already taken --
# it is a start, not a restart, and that guard is what stops a second server
# racing the first for the same socket. Continuous deployment needs the other
# behaviour: replace what is running with what was just fetched. Keeping that as
# a separate script leaves the guard in place for anyone starting by hand.

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

$listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
foreach ($listener in $listeners) {
    $processId = $listener.OwningProcess
    Write-Output "Stopping PID $processId on port $Port."
    # CloseMainWindow first would be politer, but the server runs hidden with no
    # window to close, so there is nothing gentler available here.
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
}

# Wait for the socket, not the process. A stopped process can leave the port in
# TIME_WAIT briefly, and starting into that produces a bind error that looks
# like a broken deployment.
$deadline = (Get-Date).AddSeconds($StopTimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    $still = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $still) { break }
    Start-Sleep -Milliseconds 500
}

$still = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($still) {
    throw "Port $Port is still held by PID $($still.OwningProcess) after $StopTimeoutSeconds seconds."
}

& (Join-Path $root "scripts\start_public.ps1") -Port $Port
