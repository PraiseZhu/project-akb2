# Mac Mini Deployment Notes

AKB2.0 social-trends briefing pipeline deployment guide for the company Mac Mini.

## Prerequisites

- Python 3.9+
- PyYAML: `pip install pyyaml>=6.0`
- Git access to the AKB2 repository
- iCloud Drive synced: `/Users/praise/Library/Mobile Documents/iCloud~md~obsidian/Documents/`

## Steps

### 1. Clone / pull the repository

```bash
git pull origin main
```

### 2. Install the plist

Replace `__AKB2_REPO_ROOT__` with the actual repo path before loading:

```bash
REPO_ROOT=/path/to/Project-AKB2/worktree-or-clone

sed "s|__AKB2_REPO_ROOT__|${REPO_ROOT}|g" \
    launchd/com.akb2.briefing.plist \
    > ~/Library/LaunchAgents/com.akb2.briefing.plist
```

### 3. Load the launchd agent

```bash
launchctl load ~/Library/LaunchAgents/com.akb2.briefing.plist
```

To verify it is loaded:

```bash
launchctl list | grep com.akb2.briefing
```

### 4. Vault root configuration

The plist is pre-configured for the iCloud-backed Obsidian vault:

```
/Users/praise/Library/Mobile Documents/iCloud~md~obsidian/Documents/AKB2.0
```

Verify the vault root exists and iCloud is synced before the first scheduled run.

### 5. Logs

Stdout and stderr are written to:

```
<REPO_ROOT>/logs/briefing-stdout.log
<REPO_ROOT>/logs/briefing-stderr.log
```

Create the logs directory if missing:

```bash
mkdir -p "${REPO_ROOT}/logs"
```

### 6. Scheduled run time

The agent runs daily at **07:30** local time (Asia/Shanghai for date computation). Output is written to:

```
<vault-root>/staging/candidate/<YYYY-MM-DD+1> Daily Briefing.md
<vault-root>/AKB2.0/AI TRENDS/Social/<YYYY-MM-DD+1> Daily Briefing.md  (symlink)
```

## Unload / update

```bash
launchctl unload ~/Library/LaunchAgents/com.akb2.briefing.plist
# ... update plist ...
launchctl load ~/Library/LaunchAgents/com.akb2.briefing.plist
```

## Verification (SC-7/SC-8)

After the first scheduled run, verify output:

```bash
# Check file was written
ls -la "$VAULT_ROOT/AKB2.0/AI TRENDS/Social/"

# Check frontmatter contains gate decision
grep "proposed_gate_decision" "$VAULT_ROOT/AKB2.0/AI TRENDS/Social/"*.md

# Check iCloud~md~obsidian path is accessible
ls "/Users/praise/Library/Mobile Documents/iCloud~md~obsidian/Documents/AKB2.0/"
```

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Agent not running | `launchctl list \| grep com.akb2` |
| Python module not found | Verify `PYTHONPATH=<repo>/src` in plist EnvironmentVariables |
| Gate CLI fails | Run `PYTHONPATH=src python3 src/akb2/gate/akb2_gate_check.py check --help` |
| iCloud not synced | Open Finder, check `/Users/praise/Library/Mobile Documents/iCloud~md~obsidian/` |
