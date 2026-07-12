$p=Get-CimInstance Win32_Process -Filter "Name='python.exe'"
$names=@('dashboard','monitor','synthesize','risk','execute','ingest','_watch_optimize')
foreach($n in $names){
  $hit=$p | Where-Object { $_.CommandLine -like "*$n*" -and $_.CommandLine -notlike '*-c *' }
  if($hit){ echo "ALIVE: $n (pid $($hit.ProcessId -join ','))" } else { echo "MISSING: $n" }
}
$r=(Get-CimInstance Win32_Process -Filter "Name='redis-server.exe'") -ne $null
echo "REDIS: $(if($r){'up'}else{'down'})"
