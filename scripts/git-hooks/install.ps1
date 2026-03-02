param(
  [switch]$EnableHooks
)

$ErrorActionPreference = "Stop"
$repoRoot = (git rev-parse --show-toplevel).Trim()
$hooksDir = Join-Path $repoRoot "tools/git-hooks"

function Set-HooksDisabled {
  git config --local core.hooksPath NUL
  Write-Host "[git-hooks] hooks disabled: core.hooksPath=NUL (prevents Git for Windows sh.exe issues)"
}

function Set-HooksEnabled {
  if (!(Test-Path $hooksDir)) { throw "hooks dir not found: $hooksDir" }
  git config --local core.hooksPath $hooksDir
  Write-Host "[git-hooks] hooks enabled: core.hooksPath=$hooksDir"
}

# Detect if sh.exe is blocked (common in managed Windows environments)
$gitExe = (Get-Command git).Source
$gitRoot = Split-Path (Split-Path $gitExe -Parent) -Parent
$shExe = Join-Path $gitRoot "usr\bin\sh.exe"

$shBlocked = $false
if (Test-Path $shExe) {
  try {
    & $shExe --version | Out-Null
  } catch {
    $shBlocked = $true
    Write-Host "[git-hooks] detected blocked sh.exe ($shExe): $($_.Exception.Message)"
  }
}

if ($EnableHooks -and -not $shBlocked) {
  Set-HooksEnabled
} else {
  Set-HooksDisabled
  if ($EnableHooks -and $shBlocked) {
    Write-Host "[git-hooks] cannot enable hooks because sh.exe is blocked in this environment."
  }
}

# Always pin SSH (recommended)
$sshKey = "C:/Users/$env:USERNAME/.ssh/id_ed25519"
$sshCmd = "C:/Windows/System32/OpenSSH/ssh.exe -p 443 -i $sshKey -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
git config --local core.sshCommand $sshCmd
Write-Host "[git-hooks] core.sshCommand pinned to system OpenSSH + 443"

# Ensure origin
git remote set-url origin "ssh://git@ssh.github.com:443/zhaojfifa/swiftcraft.git"
Write-Host "[git-hooks] origin set to ssh.github.com:443"
