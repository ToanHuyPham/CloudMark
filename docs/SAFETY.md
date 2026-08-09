# Safety model

CloudMark assumes benchmark machines may be blank, but it does not interpret
permission to install tools as permission to destroy data or attack third-party
systems.

## Storage

- Filesystem test-file mode is the only enabled storage mode in `0.4.0`.
- The selected directory is resolved before execution.
- Free space must cover the test file plus the larger of 1 GiB or 5% volume
  reserve.
- `fio` receives an exact filename under CloudMark's benchmark directory.
- The file and fio log files are removed in a `finally` block after completion,
  failure, timeout, cancellation, or an interrupted CLI session.
- Raw devices, TRIM, full-device preconditioning, and power-loss tests are off.

## Compute and memory

- CPU and memory profiles require explicit load confirmation.
- CPU, memory, and storage saturation suites cannot overlap locally.
- CPU duration, warm-up, prime limit, and thread count come from versioned
  profiles rather than caller-controlled command fragments.
- The native memory tool accepts only read, write, copy, and triad kernels.
- Memory profiles allocate a fixed working set and preserve at least 512 MiB of
  available memory when the operating system exposes that measurement.
- The native source is compiled in the benchmark workspace with an exact GCC
  argument list; compiler output is retained when compilation fails.
- Cancellation terminates the current child process. A failed or cancelled run
  retains completed jobs only as partial evidence.

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
- Provider throughput runs only between paired, authenticated agents.
- Agent tasks are restricted to exact iperf3 argument lists, ports 5201–5210,
  stream counts 1/4/8/16, and a 60-second per-measurement duration cap.
- Servers use one-shot mode and an independent watchdog deadline.
- Cancelling a run prevents queued work from starting; active child processes
  retain bounded task and watchdog timeouts.
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
- Independent agent credentials are returned once and stored only as hashes.
- Provider credentials and instance user-data are never included in reports.
