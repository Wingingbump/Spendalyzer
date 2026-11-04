# Starts the API and the web dev server together. Ctrl+C stops both.
$root = $PSScriptRoot

$api = Start-Process -FilePath "$root\venv\Scripts\python.exe" `
    -ArgumentList '-m','uvicorn','backend.main:app','--reload','--port','8080' `
    -WorkingDirectory $root -NoNewWindow -PassThru

$web = Start-Process -FilePath 'npm.cmd' -ArgumentList 'run','dev' `
    -WorkingDirectory "$root\frontend" -NoNewWindow -PassThru

Write-Host "`n  api  http://127.0.0.1:8080`n  web  http://localhost:5173`n"

try {
    Wait-Process -Id $api.Id, $web.Id
}
finally {
    # /T kills the child processes too. uvicorn --reload and npm both spawn the
    # process that actually holds the port, so killing only the parent leaves it
    # bound and the next run fails with "address already in use".
    foreach ($p in @($api, $web)) {
        if (-not $p.HasExited) { taskkill /PID $p.Id /T /F 2>&1 | Out-Null }
    }
}
