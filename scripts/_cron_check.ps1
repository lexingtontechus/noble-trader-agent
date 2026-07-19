$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'"
$names = @('dashboard','monitor','synthesize','risk','execute','ingest','_watch_optimize')
foreach ($n in $names) {
    $hit = $procs | Where-Object { $_.CommandLine -like "*$n*" -and $_.CommandLine -notlike '*-c *' }
    if ($hit) {
        $pids = ($hit | ForEach-Object { $_.ProcessId }) -join ','
        Write-Output ("OK   {0} -> pid(s) {1}" -f $n, $pids)
    } else {
        Write-Output ("DEAD {0}" -f $n)
    }
}
$r = Get-CimInstance Win32_Process -Filter "Name='redis-server.exe'"
if ($r) { Write-Output ("OK   redis -> pid {0}" -f $r.ProcessId) } else { Write-Output "DEAD redis" }
