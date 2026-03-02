param(
  [switch]$PinSsh
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $repoRoot

$hooksPath = "tools/git-hooks"
git config --local core.hooksPath $hooksPath

if ($PinSsh) {
  $sshCmd = "C:\Windows\System32\OpenSSH\ssh.exe -p 443 -i C:/Users/$env:USERNAME/.ssh/id_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
  git config --local core.sshCommand $sshCmd
  git remote set-url origin "ssh://git@ssh.github.com:443/zhaojfifa/swiftcraft.git"
}

Write-Host "[git-hooks] installed core.hooksPath=$hooksPath"
if ($PinSsh) {
  Write-Host "[git-hooks] ssh pinned to ssh.github.com:443 and origin updated"
}
