# Poll http://127.0.0.1:<port>/api/health (up to ~30 s), then open the app.
# Port: -Port argument, else SOUND_SYSTEM_PORT env var, else 8790.
param([int]$Port = 0)
if ($Port -le 0) {
    if ($env:SOUND_SYSTEM_PORT) { $Port = [int]$env:SOUND_SYSTEM_PORT }
    else { $Port = 8790 }
}
$base = "http://127.0.0.1:$Port"
$ErrorActionPreference = "SilentlyContinue"
for ($i = 0; $i -lt 60; $i++) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing `
            -Uri "$base/api/health" -TimeoutSec 2
        if ($r.StatusCode -eq 200) {
            Start-Process $base
            exit 0
        }
    } catch {
        Start-Sleep -Milliseconds 500
    }
}
exit 1
