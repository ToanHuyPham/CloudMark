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

- resolves Python in this order: explicit `-PythonPath`, `.venv\Scripts\python.exe`,
  the Windows `py` launcher, then a validated `python` command;
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

For Network v9, install the network pack on both Agents and restart them so
inventory reports `iperf3`, `iproute2`, `tracepath`, `ethtool`, and Linux TCP
congestion-control support. The same pack installs `dig`; when it remains
unavailable, the Run records configuration-only partial resolver evidence but
does not fail its comparison contract. A Standard Run is comparison-ineligible
when either
bounded trace does not reach its paired destination or when interface, gateway,
or source-route identity changes between the pre-load and post-load boundary.
Review the retained hop and address-class evidence as descriptive only; it does
not prove provider ownership or public-Internet transit.
Review driver queue distribution separately. An `unavailable` per-queue result
usually means that the virtual NIC does not expose a recognized `ethtool -S`
counter shape; it does not turn throughput into a failed or zero result.
Review queue steering and IRQ evidence separately. Zero configured RPS/XPS
queues can be a valid guest configuration, while `unavailable` RSS or MSI IRQ
evidence commonly means that the virtual NIC hides the control. Neither state
proves how the physical host distributes traffic, and neither invalidates the
throughput Run.
Review system-resolver observations as diagnostics only: a local stub, cache,
split DNS, or unidentified upstream prevents provider attribution from a
single fixed query.

For a repeated network campaign, keep the same Target/Generator pair and
topology declaration for the entire contract. In the Network dashboard select
`Provider Internal Network`, create a three-day campaign, and manually select
**Run next campaign window** during each authorized UTC-day test window. Review
the previous attempt before retrying a failed window. CloudMark does not run a
campaign on a timer, counts at most one comparison-eligible Run per UTC day,
and does not treat a completed fixed-pair campaign as a provider rating.

For PostgreSQL peer profiles, install the database pack on both Agents, run the
Target Agent as a non-root account, and allow TCP `55432` only from the paired
Generator. Standard Database v2 additionally requires
`pgbench_latency_log` and `procfs_process_cpu` on the Generator. Verify
`comparison_eligible`, Target `cleanup_verified`, exact tail sample count, and
Generator log cleanup before treating the Run as comparable evidence.
For `PostgreSQL Backup & Restore`, the Target additionally requires `pg_dump`,
`pg_restore`, `createdb`, `dropdb`, and `psql`. Review source/restored row-count
equality, expected scale shape, backup size, restore timing, recovery cleanup,
and final cluster cleanup. This is same-Target logical recovery evidence, not a
provider snapshot or cross-zone disaster-recovery claim.

For Web/API/TLS peer profiles, install the web pack on both Agents and run the
Target Agent as a non-root account. Allow TCP `58080` and `58443` only between
the paired Generator and Target; never expose loopback application port `58081`.
Standard Web v2 requires an Nginx build and curl build with HTTP/2 support plus
Linux procfs CPU accounting on the Generator. Verify `comparison_eligible` and
`cleanup_verified`. An HTTP/2 observation proves negotiation only and must not
be interpreted as HTTP/2 throughput.

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

### A repeated network campaign window is blocked

- read the dashboard reason code or `GET /network-campaigns/{id}`;
- confirm both contracted Agents are online and still belong to the same
  pairing session;
- confirm the selected profile is `network-peer-standard` and still reports
  the contract's profile and methodology versions;
- wait until the next UTC day when the current day already has a valid window;
- cancel or finish the existing campaign Run before another dispatch;
- create a new campaign instead of editing SQLite when pair identity or
  topology evidence changed.
- create a new campaign when the old campaign is `superseded`; CloudMark keeps
  its existing Runs but will not continue it under a different profile or
  methodology contract.

### A PostgreSQL peer run cannot start

- verify the Target reports `postgres`, `initdb`, `pg_isready`, and `pgbench`;
- verify the Generator reports `pgbench`;
- for Standard v2, verify the Generator also reports `pgbench_latency_log` and
  `procfs_process_cpu`;
- for Backup & Restore, verify the Target reports `pg_dump`, `pg_restore`,
  `createdb`, `dropdb`, and `psql`;
- restart each Agent after installing the database pack so inventory refreshes;
- verify TCP `55432` is reachable only from the Generator peer address;
- run the Target Agent as a non-root account;
- inspect the retained partial result and do not manually reuse an ephemeral
  CloudMark cluster.

If the Agent reports a residual database service directory after an abrupt
Agent or host failure, stop the Agent and verify that no PostgreSQL process is
using the CloudMark workspace. Preserve the PostgreSQL log for diagnosis, then
remove only the named `task_*` directory below the configured Agent
`database-services` workspace. Never delete the workspace root or an unknown
PostgreSQL data directory.

### A Web/API/TLS peer run cannot start

- verify the Target reports `nginx` and `openssl`; Standard also requires
  `nginx_http2`;
- verify the Generator reports `ab`; Standard also requires `curl_http2` and
  `procfs_process_cpu`;
- restart each Agent after installing the web pack so inventory refreshes;
- verify TCP `58080` and `58443` are reachable only between the paired Agents;
- run the Target Agent as a non-root account;
- verify no other saturation or paired-service suite is using either Agent.

If the Agent reports a residual web service directory after an abrupt Agent or
host failure, stop the Agent and verify that no Nginx process is using the
CloudMark workspace. Preserve `nginx-process.log` and `nginx-error.log` for
diagnosis, then remove only the named `task_*` directory below the configured
Agent `web-services` workspace. Never delete the workspace root, an unknown
Nginx directory, or a directory while its process is still active.

### Workload Suitability reports insufficient evidence

- verify that the exact Target is selected rather than the Controller host or
  another Agent;
- open the use-case detail and review every unavailable or stale hard gate;
- use the displayed Run IDs to confirm profile, methodology, target, and
  cleanup provenance;
- run only the recommended missing profiles during an authorized test window;
- do not copy a result from another Agent or manually edit SQLite to complete a
  target;
- remember that provider status remains `not-rated` after one target passes.

### Provider Comparison reports observation only

- confirm every target has the same verified provider, product/SKU, region,
  and operating-system cohort;
- confirm the selected metric uses the exact same profile, methodology, and
  unit on every Run;
- collect at least nine valid fresh samples across three targets and three UTC
  calendar days;
- use the displayed Run ID count and UTC-day count to find gaps;
- do not rename a SKU, edit timestamps, or duplicate Runs to satisfy sampling;
- remember that `comparable` describes sampling compatibility, not a provider
  rating or a winner.

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
