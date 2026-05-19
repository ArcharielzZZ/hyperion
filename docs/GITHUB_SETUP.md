# GitHub setup (ArcharielzZZ)

Local repo is configured:

| Setting | Value |
|---------|--------|
| Git user | `ArcharielzZZ` |
| Git email | `ayubiarian@gmail.com` |
| Branch | `main` |
| Remote | `https://github.com/ArcharielzZZ/hyperion.git` |

## One-time: log in to GitHub CLI

In PowerShell:

```powershell
gh auth login
```

Choose: **GitHub.com** → **HTTPS** → authenticate in the browser (or paste a token).

## Create the repo and push

```powershell
cd C:\Users\ayubi\Documents\code\MONOLITH\hyperion

gh repo create hyperion --public --source=. --remote=origin --push
```

If the repo already exists on GitHub (empty), push only:

```powershell
git push -u origin main
```

## Without `gh` (browser only)

1. Open https://github.com/new → name **hyperion** → create empty repo (no README).
2. Then:

```powershell
cd C:\Users\ayubi\Documents\code\MONOLITH\hyperion
git push -u origin main
```

Use a [Personal Access Token](https://github.com/settings/tokens) as the password when Git prompts you.
