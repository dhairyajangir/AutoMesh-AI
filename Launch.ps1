param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$localRoot = Join-Path $projectRoot '.local'
New-Item -ItemType Directory -Path $localRoot -Force | Out-Null
$env:AUTOMESH_DATA_DIR = $localRoot
$env:UV_CACHE_DIR = Join-Path $localRoot 'uv-cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $localRoot 'python'
$env:PYTHONUTF8 = '1'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw 'Python setup requires uv. Install uv from https://docs.astral.sh/uv/getting-started/installation/ then run this launcher again.'
    }
    & uv sync --frozen --python 3.12 --no-dev
    if ($LASTEXITCODE -ne 0) { throw 'Dependency setup failed. Review the error above and retry.' }
}
$distPath = Join-Path $projectRoot 'frontend\dist\index.html'
if (-not (Test-Path -LiteralPath $distPath)) {
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw 'Install Node.js 22.12+ to build the interface, then run this launcher again.' }
    Push-Location -LiteralPath (Join-Path $projectRoot 'frontend')
    try {
        & npm.cmd ci --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency setup failed.' }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    } finally { Pop-Location }
}
$studioUrl = 'http://127.0.0.1:8765'
$running = $false
try {
    $health = Invoke-RestMethod -Uri "$studioUrl/api/health" -TimeoutSec 2
    $running = $health.application -eq 'automesh-studio'
} catch { }
if (-not $running) {
    $process = Start-Process -FilePath $pythonPath -ArgumentList @('-m', 'uvicorn', 'automesh.api:app', '--host', '127.0.0.1', '--port', '8765', '--log-level', 'info') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $localRoot 'server.log') -RedirectStandardError (Join-Path $localRoot 'server-error.log') -PassThru
    $process.Id | Set-Content -LiteralPath (Join-Path $localRoot 'server.pid')
    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        Start-Sleep -Milliseconds 500
        if ($process.HasExited) { throw "AutoMesh stopped during startup. Read $localRoot\server-error.log" }
        try {
            $health = Invoke-RestMethod -Uri "$studioUrl/api/health" -TimeoutSec 1
            if ($health.application -eq 'automesh-studio') { $running = $true; break }
        } catch { }
    }
    if (-not $running) { throw "Startup did not finish. Read $localRoot\server-error.log" }
}
Write-Host "AutoMesh Studio is running at $studioUrl"
Write-Host 'Your projects and model files are in .local. Run Stop.ps1 to stop the local engine.'
if (-not $NoBrowser) { Start-Process $studioUrl }
