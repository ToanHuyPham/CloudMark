# Safety model

CloudMark assumes benchmark machines may be blank, but it does not interpret
permission to install tools as permission to destroy data or attack third-party
systems.

## Storage

- Filesystem test-file mode is the only enabled mode in `0.2.0`.
- The selected directory is resolved before execution.
- Free space must cover the test file plus the larger of 1 GiB or 5% volume
  reserve.
- `fio` receives an exact filename under CloudMark's benchmark directory.
- The file and fio log files are removed in a `finally` block after completion,
  failure, timeout, cancellation, or an interrupted CLI session.
- Raw devices, TRIM, full-device preconditioning, and power-loss tests are off.

## Runner controls

- Every run has a bounded timeout between 30 seconds and 12 hours.
- Cancellation terminates the active child process and then performs cleanup.
- Commands are passed as argument arrays with `shell=False`.
- Completed jobs are retained as partial evidence, but cancelled or failed runs
  are never treated as complete assessment results.
- A Controller restart marks stale queued/running jobs as failed and interrupted.

## Network

- There is no arbitrary target-IP load endpoint.
- The project policy disables cloud-to-controller measurements.
- Provider throughput will run only between paired agents.
- Public DDoS, spoofing, reflection, and amplification are outside project
  scope. Future resilience tests require authenticated, operator-owned targets
  with enforced rate and duration caps.

## Bootstrap

- Package manager commands are predefined lists, not shell strings.
- Preview is the default. Installation requires `bootstrap --yes` and
  administrator/root privileges.
- Installed package names and commands are visible in the preview.

## Secrets

- The local controller token is stored in `.cloudmark/controller.token` and is
  excluded from Git.
- The browser retains it only in `sessionStorage`.
- Pairing secrets are stored as hashes in SQLite.
- Provider credentials and instance user-data are never included in reports.
