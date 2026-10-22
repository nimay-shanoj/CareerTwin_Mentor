<#
.SYNOPSIS
    Start, stop and check the CareerTwin development server.

.DESCRIPTION
    The server has to run as a detached background process, otherwise it dies
    with the terminal that started it. This script does that properly:

      * it records the process id in a file, so it can be stopped reliably
      * it writes the logs to the temp folder, NOT into the project - the
        Flask reloader watches the project directory, and a log file that
        changes inside it can trigger a restart loop
      * "stop" kills the whole process tree, because the reloader runs the
        app in a child process

.PARAMETER Action
    start | stop | restart | status | logs

.EXAMPLE
    .\tools\serve.ps1 start
    .\tools\serve.ps1 status
    .\tools\serve.ps1 stop
#>
param(
    [ValidateSet("start", "stop", "restart", "status", "logs")]
    [string]$Action = "status"
)

$ErrorActionPreference = "Stop"

$Here    = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root    = Split-Path -Parent $Here          # .../frontend
$Repo    = Split-Path -Parent $Root          # the repository
$Python  = Join-Path $Root ".venv\Scripts\python.exe"
$PidFile = Join-Path $env:TEMP "careertwin-server.pid"
$LogFile = Join-Path $env:TEMP "careertwin-server.log"
$ErrFile = Join-Path $env:TEMP "careertwin-server.err"

function Get-ServerPid {
    if (Test-Path $PidFile) {
        $serverPid = (Get-Content $PidFile -Raw).Trim()
        if ($serverPid -and (Get-Process -Id $serverPid -ErrorAction SilentlyContinue)) {
            return [int]$serverPid
        }
    }
    return $null
}

function Stop-Server {
    $serverPid = Get-ServerPid
    if (-not $serverPid) {
        Write-Host "Not running."
        return
    }
    Write-Host "Stopping (pid $serverPid) ..."
    # /T kills the reloader child as well.
    & taskkill /PID $serverPid /T /F | Out-Null
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    Write-Host "Stopped."
}

function Start-Server {
    if (Get-ServerPid) {
        Write-Host "Already running (pid $(Get-ServerPid))."
        return
    }
    if (-not (Test-Path $Python)) {
        Write-Host "No virtualenv found at $Python" -ForegroundColor Red
        Write-Host "Create it first:  python -m venv .venv ; .venv\Scripts\pip install -r requirements.txt"
        return
    }

    Push-Location $Root
    try {
        $process = Start-Process -FilePath $Python `
            -ArgumentList "run.py" `
            -WorkingDirectory $Root `
            -RedirectStandardOutput $LogFile `
            -RedirectStandardError $ErrFile `
            -WindowStyle Hidden `
            -PassThru
    }
    finally {
        Pop-Location
    }

    $process.Id | Out-File -FilePath $PidFile -Encoding ascii

    # Wait for the port to answer. A raw TCP connect fails fast and is not
    # affected by any system proxy, unlike Invoke-WebRequest.
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            $client = New-Object System.Net.Sockets.TcpClient
            $client.Connect("127.0.0.1", 5000)
            $client.Close()
            $ready = $true
            break
        }
        catch { }
    }

    if ($ready) {
        Write-Host "Started (pid $($process.Id))." -ForegroundColor Green
        Write-Host "  web app : http://127.0.0.1:5000"
        Write-Host "  raw TCP : 127.0.0.1:9000"
        Write-Host "  logs    : $LogFile"
    }
    else {
        Write-Host "Did not come up in time - check $LogFile" -ForegroundColor Red
        Get-Content $LogFile -Tail 20 -ErrorAction SilentlyContinue
    }
}

switch ($Action) {
    "start"   { Start-Server }
    "stop"    { Stop-Server }
    "restart" { Stop-Server; Start-Sleep -Seconds 1; Start-Server }
    "logs"    { Get-Content $LogFile -Tail 40 -ErrorAction SilentlyContinue }
    "status"  {
        $serverPid = Get-ServerPid
        if ($pid) {
            Write-Host "Running (pid $serverPid)."
            try {
                $client = New-Object System.Net.Sockets.TcpClient
                $client.Connect("127.0.0.1", 5000)
                $client.Close()
                Write-Host "  health  : port 5000 is accepting connections"
            }
            catch { Write-Host "  health  : not answering" -ForegroundColor Yellow }
        }
        else {
            Write-Host "Not running."
        }
    }
}

# The background server inherits this console's handles, which would keep
# the shell alive. Leave explicitly.
exit 0
