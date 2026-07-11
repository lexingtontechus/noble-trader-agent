# =============================================================================
# trading_stack.ps1 — Windows-native equivalent of trading_stack.sh.
# Uses PowerShell background jobs instead of nohup. Run from the repo root
# (git-bash is fine too; this is for Task Scheduler / pwsh users).
#
# Usage:  .\scripts\trading_stack.ps1 -Action start|stop|restart|status
# Prereqs: .venv present, Redis up, .env filled, `platform init` run once.
# =============================================================================
param(
  [ValidateSet('start','stop','restart','status')]
  [string]$Action = 'start',
  [int]$Equity = 100000
)

$REPO = "C:\Users\aloys\AppData\Local\hermes\profiles\noble-agent\noble-trader-agent\repo"
$LOGDIR = Join-Path $REPO "logs"
$PIDDIR = Join-Path $REPO ".pids"
New-Item -ItemType Directory -Force -Path $LOGDIR, $PIDDIR | Out-Null

$procs = @(
  @{name='dashboard';  args='dashboard --host 127.0.0.1 --port 8080'},
  @{name='ingest';     args='ingest'},
  @{name='monitor';    args='monitor'},
  @{name='synthesize'; args='synthesize'},
  @{name='risk';       args="risk --equity $Equity"},
  @{name='execute';    args="execute --equity $Equity --paper"}
)

function Start-Proc($p) {
  $pidFile = Join-Path $PIDDIR "$($p.name).pid"
  if (Test-Path $pidFile) {
    $old = Get-Content $pidFile
    if (Get-Process -Id $old -ErrorAction SilentlyContinue) {
      Write-Host "[$(Get-Date -f HH:mm:ss)] $($p.name) already running ($old)"; return
    }
  }
  Write-Host "[$(Get-Date -f HH:mm:ss)] launch $($p.name): platform $($p.args)"
  $job = Start-Process -FilePath "platform" -ArgumentList $p.args `
    -RedirectStandardOutput (Join-Path $LOGDIR "$($p.name).log") `
    -RedirectStandardError  (Join-Path $LOGDIR "$($p.name).err") -PassThru
  $job.Id | Out-File -FilePath $pidFile
}

function Stop-All {
  Write-Host "[$(Get-Date -f HH:mm:ss)] stopping all loop processes"
  foreach ($p in $procs) {
    $pidFile = Join-Path $PIDDIR "$($p.name).pid"
    if (Test-Path $pidFile) {
      $id = Get-Content $pidFile
      Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
      Write-Host "  stopped $($p.name) ($id)"
      Remove-Item $pidFile
    }
  }
}

function Show-Status {
  foreach ($p in $procs) {
    $pidFile = Join-Path $PIDDIR "$($p.name).pid"
    if (Test-Path $pidFile -and (Get-Process -Id (Get-Content $pidFile) -ErrorAction SilentlyContinue)) {
      Write-Host "  $($p.name): UP ($(Get-Content $pidFile))"
    } else { Write-Host "  $($p.name): DOWN" }
  }
  & platform health
}

switch ($Action) {
  'start'   { $procs | ForEach-Object { Start-Proc $_ }; Write-Host "Loop running. Monitor via 'platform health' or dashboard http://127.0.0.1:8080" }
  'stop'    { Stop-All }
  'restart' { Stop-All; Start-Sleep 2; $procs | ForEach-Object { Start-Proc $_ } }
  'status'  { Show-Status }
}
