# CloudMark operations runbook

## 1. Purpose

This runbook starts, verifies, stops, backs up, and restores the local CloudMark
Controller and dashboard. It deliberately excludes live credentials and active
provider addresses.

## 2. Prerequisites

- Windows PowerShell 5.1 or newer;
- Python 3.9 or newer;
- Node.js 22 or newer;
- pnpm 11 for dependency installation;
- a cloned CloudMark repository;
- a protected runtime snapshot when prior history must be restored.

## 3. First-time setup

```powershell
Set-Location C:\path\to\CloudMark
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
pnpm install
```

Do not copy a secret-bearing runtime snapshot until the destination machine and
account are trusted.

## 4. Restore an existing runtime

Run this before starting the Controller:

```powershell
.\scripts\restore-runtime.ps1 `
  -BackupPath 'D:\Protected\CloudMark-runtime-YYYYMMDD-HHMMSS'
```

If `.cloudmark` already contains data, the restore script refuses to overwrite
it unless `-Replace` is supplied. Replacement moves the existing directory to
a timestamped recovery path instead of deleting it.

## 5. Start the local stack

```powershell
.\scripts\start-local.ps1
```

The launcher:

- uses `.venv\Scripts\python.exe` by default;
- starts the Controller on `127.0.0.1:8787`;
- selects the first available dashboard port from 3000 through 3010;
- writes logs and a process record under `.tmp/local`;
- waits for both health checks;
- does not print the Controller token.

Retrieve the token locally only when the dashboard needs it:

```powershell
(Get-Content '.cloudmark\controller.token' -Raw).Trim()
```

Paste it into **Controller key**. Never paste it into a public issue, Git commit,
or handoff document.

## 6. Status and health

```powershell
Invoke-RestMethod http://127.0.0.1:8787/api/v1/health
Get-Content .tmp\local\processes.json
Get-Content .tmp\local\controller.err.log -Tail 100
Get-Content .tmp\local\dashboard.err.log -Tail 100
```

The dashboard URL is recorded in `.tmp/local/processes.json`. Development
origins are limited to localhost ports 3000 through 3010.

## 7. Stop the local stack

```powershell
.\scripts\stop-local.ps1
```

The script stops only PIDs recorded by `start-local.ps1` and validates their
command lines before termination. It does not stop an unrelated process merely
because that process uses a familiar executable name.

## 8. Runtime backup

Evidence-only snapshot:

```powershell
.\scripts\backup-runtime.ps1 -DestinationRoot 'D:\Protected'
```

Complete snapshot including Controller token and SSH material:

```powershell
.\scripts\backup-runtime.ps1 `
  -DestinationRoot 'D:\Protected' `
  -IncludeSecrets `
  -AcknowledgeSensitiveBackup
```

The backup uses SQLite's online backup API, so the evidence database remains
consistent even when the Controller is running. Complete snapshots are
sensitive. Store them only on encrypted media or encrypt the resulting folder
immediately.

Never choose a destination inside the CloudMark repository.

## 9. Remote Agent recovery

Restoring the local database and SSH material does not prove that an old target
still exists. Before reconnecting:

1. verify the provider VM identity in the provider console;
2. verify its current external or management address;
3. verify the SSH host key through a trusted channel;
4. restore or recreate the private Controller-to-Agent transport;
5. start the persistent Agent with placeholders from
   `docs/REMOTE_EXECUTION.md`;
6. confirm that the dashboard shows the intended Agent as online;
7. select that Agent explicitly before running a suite.

Do not place the real address, session token, join token, or private key in this
repository.

## 10. Benchmark operating procedure

1. Keep the target idle and finish package updates before the run window.
2. Confirm the intended Agent is online.
3. Run a Quick profile after provisioning or recovery.
4. Run one Standard or specialized saturation suite at a time.
5. Record the Run ID.
6. Review heartbeat, current job, latency percentiles, stability, and cleanup.
7. Preserve failed runs; diagnose them before retrying.
8. Repeat official profiles in multiple time windows.

## 11. Troubleshooting

### API is offline

- inspect `.tmp/local/controller.err.log`;
- verify port 8787 is not owned by another application;
- rerun `python -m cloudmark serve --data-dir .cloudmark` in the foreground.

### Dashboard is offline

- inspect `.tmp/local/dashboard.err.log`;
- verify Node.js 22+ and `node_modules`;
- run `pnpm install`, then `pnpm run dev` in the foreground.

### Agent is offline

- verify the private management transport or reverse tunnel;
- verify the persistent Agent process;
- check Controller and Agent clocks;
- verify that credentials came from the protected runtime, not documentation;
- do not create firewall rules open to the entire Internet merely to recover a
  benchmark session.

### A benchmark failed

- retain the Run ID and partial evidence;
- inspect the Controller error and Agent heartbeat;
- confirm free space and tool versions;
- do not immediately rerun a write-heavy or saturation profile;
- verify cleanup before the next run.

## 12. Optional Codex local environment

In the ChatGPT desktop app, open the local-project environment settings and
configure the checked-in scripts as common actions:

- setup: create `.venv`, install the Python package, and run `pnpm install`;
- start: `.\scripts\start-local.ps1`;
- stop: `.\scripts\stop-local.ps1`;
- verify: run the Python unit tests and `pnpm test`.

The desktop app writes its generated project environment configuration under
`.codex`. Review the generated file for machine-specific paths or secrets before
committing it. Do not hand-write an undocumented schema or commit a configuration
containing credentials. The checked-in scripts remain the portable source of
truth when no generated local-environment file is present.
