# CloudMark recovery and transfer

## Recovery model

CloudMark uses three independent recovery layers:

1. **Git repository:** source, tests, public documentation, and durable Codex
   guidance.
2. **Protected runtime snapshot:** SQLite evidence, and optionally local tokens
   and SSH material.
3. **Provider-side inventory:** VM identity, firewall intent, current addresses,
   and provider-console ownership.

No chat transcript, local process, VM, or storage location is sufficient by
itself.

## Before losing access to the current Codex installation

1. Review `git status` and commit intended changes.
2. Push the intended branch to the canonical GitHub repository.
3. Update `docs/CURRENT_STATE.md`.
4. Create an evidence-only runtime snapshot.
5. Create a complete secret-bearing snapshot when remote access must be
   recoverable.
6. Encrypt and copy snapshots to at least two controlled locations.
7. Record provider resources in a private password manager or infrastructure
   inventory, not in this public repository.
8. Optionally archive the chat transcript as historical reference.

## Create snapshots

```powershell
.\scripts\backup-runtime.ps1 -DestinationRoot 'D:\Protected'

.\scripts\backup-runtime.ps1 `
  -DestinationRoot 'D:\Protected' `
  -IncludeSecrets `
  -AcknowledgeSensitiveBackup
```

Each snapshot contains `manifest.json` and a consistent
`cloudmark.sqlite3`. Secret-bearing snapshots also contain `controller.token`
and the `ssh` directory when present.

## Restore on another machine

```powershell
git clone https://github.com/ToanHuyPham/CloudMark.git
Set-Location CloudMark

py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
pnpm install

.\scripts\restore-runtime.ps1 `
  -BackupPath 'D:\Protected\CloudMark-runtime-YYYYMMDD-HHMMSS'

.\scripts\start-local.ps1
```

Open the dashboard URL printed by the launcher and enter the locally retrieved
Controller token.

## Continue with another Codex

Add the cloned folder as a local project and make it the primary folder. Start a
new task with:

```text
Read AGENTS.md completely. Then read docs/PROJECT_HANDOFF.md,
docs/CURRENT_STATE.md, docs/OPERATIONS_RUNBOOK.md, and docs/DECISIONS.md.
Inspect Git status and verify the environment. Do not run benchmarks or contact
provider systems without explicit authorization. Report the current state and
the next safe action.
```

If an old transcript is available, provide it only as supplemental context.
Repository guidance, Git history, and the runtime manifest are the sources of
truth.

## Validation after restore

```powershell
Invoke-RestMethod http://127.0.0.1:8787/api/v1/health
python -m unittest discover -s tests_python -v
pnpm test
```

Then verify in the dashboard:

- historical runs are visible;
- the Controller host inventory loads;
- no Agent is assumed online solely because it exists in history;
- the intended target must report a fresh heartbeat before selection;
- secret values do not appear in tracked files or logs intended for sharing.

## Rotation policy

- create an evidence snapshot after every official provider test window;
- create a complete protected snapshot after Controller identity, SSH material,
  or remote-session configuration changes;
- retain at least the latest known-good snapshot and one older snapshot;
- periodically test restoration into a disposable directory;
- remove obsolete secret-bearing snapshots using the storage provider's secure
  deletion or cryptographic-key rotation process.
