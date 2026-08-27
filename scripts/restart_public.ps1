param(
    [int]$Port = 8077,
    [int]$StopTimeoutSeconds = 20,
    [string]$Data = "",
    [string]$Artifacts = ""
)

# Stop whatever is serving, then start it again the same way.
#
# `start_public.ps1` exits early when the port is already taken -- it is a start,
# not a restart, and that guard is what stops a second server racing the first
# for the same socket. Continuous deployment needs the opposite, so this is a
# separate script rather than a flag that would weaken the guard for everyone.
#
# The important part is that it carries the running server's --data and
# --artifacts across. Those arguments default to the checkout the launcher lives
# in, and a deployment is served from a checkout with no data/ of its own. A
# restart that dropped them looked completely healthy -- the port answered, the
# gate answered 401 -- while serving an empty data directory, with every dataset
# still on disk and invisible. Nothing about that failure announces itself, so
# preserving the arguments is the fix rather than remembering to pass them.

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Get-LauncherArgument {
    param([string]$CommandLine, [string]$Name)
    # The launcher is started with plain unquoted paths, so a simple split is
    # enough here and avoids pulling in a command-line parser for two values.
    $match = [regex]::Match($CommandLine, "--$Name\s+(?<value>`"[^`"]+`"|\S+)")
    if (-not $match.Success) { return "" }
    return $match.Groups["value"].Value.Trim('"')
}

$listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
foreach ($listener in $listeners) {
    $processId = $listener.OwningProcess
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue
    if ($process -and $process.CommandLine) {
        # Only adopt what the caller did not specify; an explicit argument wins.
        if (-not $Data) { $Data = Get-LauncherArgument $process.CommandLine "data" }
        if (-not $Artifacts) { $Artifacts = Get-LauncherArgument $process.CommandLine "artifacts" }
    }
    Write-Output "Stopping PID $processId on port $Port."
    # CloseMainWindow would be politer, but the server runs hidden with no window
    # to close, so there is nothing gentler available.
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
}

# Wait for the socket, not the process. A stopped process can leave the port in
# TIME_WAIT briefly, and starting into that produces a bind error that reads as a
# broken deployment rather than as timing.
$deadline = (Get-Date).AddSeconds($StopTimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    if (-not (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 500
}

$still = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($still) {
    throw "Port $Port is still held by PID $($still.OwningProcess) after $StopTimeoutSeconds seconds."
}

if ($Data) { Write-Output "Serving data from $Data." }
& (Join-Path $root "scripts\start_public.ps1") -Port $Port -Data $Data -Artifacts $Artifacts
