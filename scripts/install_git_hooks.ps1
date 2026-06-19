$ErrorActionPreference = "Stop"

$hooksDir = ".githooks"
New-Item -ItemType Directory -Force -Path $hooksDir | Out-Null

Set-Content -Encoding ascii -Path "$hooksDir/pre-commit" -Value @'
#!/bin/sh
exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/git-hooks/pre-commit.ps1
'@

Set-Content -Encoding ascii -Path "$hooksDir/pre-push" -Value @'
#!/bin/sh
exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/git-hooks/pre-push.ps1 "$@"
'@

git config core.hooksPath .githooks
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to configure Git hooks path."
    exit $LASTEXITCODE
}

Write-Host "Git hooks path configured: .githooks"
Write-Host "pre-commit runs batch pre-commit gate; pre-push runs batch post-commit gate."
