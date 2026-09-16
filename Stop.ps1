$ErrorActionPreference = 'Stop'
$pidPath = Join-Path $PSScriptRoot '.local\server.pid'
if (-not (Test-Path -LiteralPath $pidPath)) { Write-Host 'No launcher-owned AutoMesh process was recorded.'; exit }
$studioProcessId = [int](Get-Content -LiteralPath $pidPath)
$studioProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $studioProcessId"
if ($studioProcess -and $studioProcess.CommandLine -like '*automesh.api:app*' -and $studioProcess.ExecutablePath -eq (Join-Path $PSScriptRoot '.venv\Scripts\python.exe')) {
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $studioProcessId"
    foreach ($child in $children) {
        if ($child.ExecutablePath -eq $studioProcess.ExecutablePath) { Stop-Process -Id $child.ProcessId -ErrorAction SilentlyContinue }
    }
    Stop-Process -Id $studioProcessId -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $pidPath
    Write-Host 'AutoMesh stopped. Saved projects are unchanged.'
} elseif (-not $studioProcess) {
    Remove-Item -LiteralPath $pidPath
    Write-Host 'AutoMesh is already stopped.'
} else { throw 'The recorded process no longer belongs to this AutoMesh checkout; it was not stopped.' }
