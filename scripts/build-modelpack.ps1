# scripts/build-modelpack.ps1 — build Safa-models-{core|extra}-<ver>.zip
# from the local models/ tree via backend.core.model_pack.
#
#   scripts\build-modelpack.ps1           # core pack only
#   scripts\build-modelpack.ps1 -All      # core + extra packs (= every model)
param([switch]$All)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$ver = (Select-String -Path pyproject.toml -Pattern '^version = "(.*)"').Matches[0].Groups[1].Value
New-Item -ItemType Directory -Force dist_pkg | Out-Null

# Model sets come from release-info.toml [model_packs] — never hardcode.
$packMap = @{}
& uv run --no-sync python -m backend.core.release_info model_packs |
    ForEach-Object { $k, $v = $_ -split "=", 2; $packMap[$k] = $v }
$packNames = @("core") + $(if ($All) { @("extra") } else { @() })
foreach ($p in $packNames) {
    $names = $packMap[$p]
    $out = "dist_pkg\Safa-models-$p-$ver.zip"
    Write-Host "== $out"
    & uv run --no-sync python -m backend.core.model_pack build $out --names $names
    if ($LASTEXITCODE -ne 0) { throw "pack $p failed" }
    $item = Get-Item $out
    $item | Select-Object Name, @{n="MB";e={[math]::Round($_.Length/1MB,1)}}
    # GitHub rejects release assets >= 2 GiB; fail here rather than have
    # the publish job silently drop the pack.
    if ($item.Length -ge 2047MB) { throw "pack $p is $([math]::Round($item.Length/1MB)) MB - over the 2 GiB release cap; split it in release-info.toml" }
}
