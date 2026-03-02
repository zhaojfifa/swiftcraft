# Git Push Stability

## Install versioned hooks

```powershell
pwsh scripts/git-hooks/install.ps1
```

This sets:

- `core.hooksPath=tools/git-hooks`

## Optional: pin SSH over 443 and normalize origin

```powershell
pwsh scripts/git-hooks/install.ps1 -PinSsh
```

This additionally sets:

- `core.sshCommand=C:\Windows\System32\OpenSSH\ssh.exe -p 443 -i C:/Users/$env:USERNAME/.ssh/id_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new`
- `origin=ssh://git@ssh.github.com:443/zhaojfifa/swiftcraft.git`

## Skip hooks temporarily

```powershell
$env:SKIP_GIT_HOOKS="1"
git push
Remove-Item Env:SKIP_GIT_HOOKS
```

## Troubleshooting

1. Check active hook path:
   - `git config --local --get core.hooksPath`
2. Check active SSH command:
   - `git config --local --get core.sshCommand`
3. Check remote URL:
   - `git remote -v`
4. If push still fails, test SSH reachability:
   - `C:\Windows\System32\OpenSSH\ssh.exe -p 443 -i C:/Users/$env:USERNAME/.ssh/id_ed25519 -o IdentitiesOnly=yes -T git@ssh.github.com`
