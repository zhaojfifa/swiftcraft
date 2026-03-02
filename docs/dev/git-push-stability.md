# Git Push Stability

## Install versioned hooks

```powershell
pwsh scripts/git-hooks/install.ps1
```

This sets:

- `core.hooksPath=NUL` by default (avoids Git for Windows `sh.exe` issues in restricted environments)
- `core.sshCommand` pinned to system OpenSSH over port `443`
- `origin=ssh://git@ssh.github.com:443/zhaojfifa/swiftcraft.git`

## Optional: enable hooks explicitly

```powershell
pwsh scripts/git-hooks/install.ps1 -EnableHooks
```

When `sh.exe` is available, this sets:

- `core.hooksPath=tools/git-hooks`

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
