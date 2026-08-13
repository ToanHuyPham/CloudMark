# CloudMark user guide

This guide applies to version `0.5.0`. The current release provides system
inventory, AWS/Azure/Google Cloud metadata detection, tool bootstrap planning,
versioned CPU and memory-bandwidth assessment, filesystem-safe storage
assessment through `fio`, SQLite history, multi-system topology registration,
authenticated persistent agents, remote CPU/memory/storage dispatch, guarded
two-direction TCP testing, and a local dashboard. The dashboard distinguishes
`Available`, `Partial`, and `Roadmap`; unavailable executors are never scored or
presented as ready.

CloudMark covers 17 domains across cloud instances, VPS systems, and bare metal:
inventory, provider identity, virtualization, CPU, memory/NUMA, storage, network,
GPU, web/API, database/cache, containers/Kubernetes, security, HA/DR,
observability, control plane, cost, and consistency/noisy-neighbor behavior.
Storage is the first mature executor, not the limit of the product.

## 1. Deployment models

### Assess one system

```text
Controller/dashboard on the system under assessment
```

Use this model for inventory, provider detection, CPU/memory evidence, GPU
inventory, and local or block storage. An Agent is not required for the local
executors.

### Assess a provider using multiple VMs

```text
Operator system: Controller + dashboard
Provider:       VM A (target) ↔ VM B (generator)
Optional:       VM C (replica/failover)
```

The Controller never receives cloud benchmark traffic. Network benchmark data
flows directly between VM A and VM B.

In version `0.5.0`, dashboard-triggered CPU, memory, and storage suites execute
on the explicitly selected target: either the Controller host or one
authenticated Agent. The selection, Agent identity, target inventory, provider
evidence, and Agent version are retained with the result.

## 2. Requirements

### Controller system

- Windows, Linux, or macOS;
- Python 3.9 or newer;
- Node.js 22 or newer;
- pnpm;
- a modern browser.

### Agent system

- Ubuntu or Debian;
- RHEL or CentOS-compatible distributions;
- SLES 12.5 or 15;
- Windows for the Controller and inventory, with partial benchmark automation;
- `root`, `sudo`, or Administrator access for tool bootstrap.

Agents do not require Node.js or the dashboard.

## 3. Install the Controller

Clone the repository:

```bash
git clone https://github.com/ToanHuyPham/CloudMark.git
cd CloudMark
```

Create an isolated Python environment if required.

### Linux or macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Install the dashboard dependencies:

```bash
pnpm install
```

## 4. Start CloudMark locally

On Windows, the recoverable launcher starts both processes, selects an available
dashboard port from 3000 through 3010, writes logs under `.tmp/local`, and does
not print the Controller token:

```powershell
.\scripts\start-local.ps1
```

Use `.\scripts\stop-local.ps1` to stop only the processes recorded by that
launcher. See [`OPERATIONS_RUNBOOK.md`](OPERATIONS_RUNBOOK.md) and
[`RECOVERY.md`](RECOVERY.md) for backup, restoration, and transfer to another
machine or Codex installation.

The manual two-terminal procedure remains available below.

Open the first terminal:

```bash
python -m cloudmark serve --data-dir .cloudmark
```

Expected output:

```text
CloudMark API: http://127.0.0.1:8787/api/v1/health
Controller token: <TOKEN>
Policy: cloud-to-controller network measurement is disabled.
```

Never publish the Controller token in GitHub, logs, or public chat.

Open a second terminal:

```bash
pnpm run dev
```

Open the printed URL, usually `http://localhost:3000`. If that port is busy,
the dashboard may select 3001, 3002, and so on. The local API accepts dashboard
origins on ports 3000–3010.

In the dashboard:

1. Select **Controller key**.
2. Paste the token printed by the API terminal.
3. Select **Connect**.
4. The token remains only in the current browser-tab session.

### Understanding the dashboard

- **Overview** shows live evidence for the connected system and assessment
  readiness. CPU, memory, storage, and network cards do not represent the full
  product scope.
- **Assessment Catalog** lists all 17 technical domains and their `Available`,
  `Partial`, or `Roadmap` state.
- **Compute & Memory** runs CPU integer scaling/sustained profiles and native
  cache-resistant memory-bandwidth profiles with an explicit local/Agent target,
  live progress, and cancellation.
- **Storage Assessment** provides Quick, Standard, Database, Throughput, and
  Sustained profiles with live progress and cancellation.
- **Distributed Testing** creates an authenticated multi-agent topology and
  runs guarded path evidence, TCP, UDP, idle-latency, and simultaneous
  bidirectional profiles plus read-only NIC, driver per-queue counters, and
  TCP-control evidence. Manual repeated UTC-day campaigns are available.
  Network remains `Partial` because cross-pair automation, physical-fabric
  verification, wider queue/affinity normalization, administrative public-path
  classification, and mTLS enrollment are incomplete.
- **Database Assessment** uses the same paired Agents for an isolated
  PostgreSQL service and guarded pgbench workloads. PostgreSQL remains
  `Partial` until tail-percentile, replication, recovery, MySQL, and Redis
  methodologies are implemented.
- **Web & API Assessment** starts an isolated Nginx service on the Target and
  runs fixed HTTP, HTTPS, connection-churn, JSON, and static-transfer workloads
  from the Generator. It reports request rate, failures, P50–P99 latency, TLS,
  transfer, and cleanup evidence while rejecting arbitrary URLs and DDoS load.
- **Workload Suitability** maps technical evidence to 12 use cases. Missing
  required metrics return `Insufficient evidence`, not zero.
- **History** lists locally retained Runs. Dashboard polling uses compact
  presentation summaries; complete raw evidence and full-resolution time
  series remain in SQLite and are available from `/api/v1/runs/{id}` so
  conclusions can be recalculated when the methodology changes.

See [`ASSESSMENT_CATALOG.md`](ASSESSMENT_CATALOG.md) for the complete metric and
minimum-topology matrix.

## 5. Collect inventory

Run from the CLI:

```bash
python -m cloudmark inventory
```

Or select **Rescan** in the dashboard.

Current inventory includes:

- hostname, OS, kernel, distribution, and architecture;
- CPU model and logical core count;
- total memory;
- OS-visible volumes and disks;
- local IP addresses;
- virtualization evidence when exposed by the OS;
- availability of `fio`, `iperf3`, `sysbench`, Docker, and Podman.

Cloud detection probes AWS IMDSv2, Azure IMDS, and Google Compute metadata. If
trusted evidence is unavailable, the result is `Unknown`; CloudMark does not
guess from an IP address.

For regional or self-hosted clouds without standard metadata, place a manifest
based on `examples/provider-manifest.json` at `/etc/cloudmark/provider.json`,
`C:\ProgramData\CloudMark\provider.json`, or set
`CLOUDMARK_PROVIDER_MANIFEST`. An unsigned manifest is always labeled
`declared, unverified` with lower confidence than trusted provider metadata.

## 6. Inspect dependencies before installation

```bash
python -m cloudmark doctor --packs compute,memory,storage,network,database,web
```

This command displays a plan and does not modify the system.

| Pack | Contents |
|---|---|
| `base` | curl, jq, dmidecode, sysstat, numactl |
| `compute` | sysbench |
| `memory` | GCC and the OpenMP runtime |
| `storage` | fio, smartmontools, nvme-cli |
| `network` | iperf3, ethtool, mtr, DNS tools |
| `database` | sysbench, PostgreSQL server/client tools including pgbench, Redis |
| `web` | Nginx, ApacheBench, and OpenSSL |

## 7. Bootstrap tools

### Ubuntu or Debian

```bash
sudo python -m cloudmark bootstrap \
  --packs compute,memory,storage,network,database,web \
  --yes
```

### RHEL or CentOS

CloudMark detects `dnf` or `yum` automatically:

```bash
sudo python -m cloudmark bootstrap --packs compute,memory,storage,network,database,web --yes
```

Some packages such as `sysbench` may require an additional repository. If the
package manager rejects the operation, bootstrap stops and preserves the error.

### SLES 12.5 or 15

```bash
sudo python -m cloudmark bootstrap --packs compute,memory,storage,network,database,web --yes
```

SLES may require valid registration. When a repository does not provide a tool,
use a supported offline bundle after the project publishes one.

Check `python3 --version` before installation. CloudMark requires Python 3.9 or
newer. If SLES 12.5 provides an older system runtime, use an organization-managed
Python 3.9+ runtime or offline bundle instead of replacing the system Python.

### Windows

CloudMark detects `winget`, but does not yet map every portable `fio` and
`iperf3` package automatically. Inventory and the Controller are operational;
the dashboard marks Windows benchmark automation as `Partial` until the package
mapping is complete.

## 8. Run compute and memory assessments

Install the two execution packs:

```bash
sudo python -m cloudmark bootstrap --packs compute,memory --yes
```

Run preflight without generating load:

```bash
python -m cloudmark run compute --profile compute-quick
python -m cloudmark run memory --profile memory-quick
```

Execute the quick profiles on an idle assessment system:

```bash
python -m cloudmark run compute --profile compute-quick --yes
python -m cloudmark run memory --profile memory-quick --yes
```

The same controls are available under **Compute & Memory** in the dashboard.
Select **Controller host** for local execution or an online Agent for provider
execution. Only one saturation suite (compute, memory, or storage) can be queued
or running on the same target at a time. Cancellation stops the active child
process and preserves completed jobs as partial evidence.

The CPU profile records single-core and all-core event rate, scaling efficiency,
P95 latency, one-second stability, and Linux host telemetry. The memory profile
compiles the packaged C/OpenMP benchmark, uses a fixed 384 MiB allocation in
quick mode, and preserves a 512 MiB available-memory reserve. Standard mode uses
a 768 MiB allocation and more read, write, copy, and triad phases.

Do not compare results across CPU architectures as if the event represents
identical work. Match the profile, tool version, architecture, OS/power context,
and background-load policy. See
[`COMPUTE_MEMORY_METHODOLOGY.md`](COMPUTE_MEMORY_METHODOLOGY.md) for the complete
validity contract and current limitations.

The native memory executor currently targets Linux with GCC/OpenMP. Windows is
supported for the Controller and inventory, but version `0.5.0` does not claim
complete Windows CPU/memory qualification.

## 9. Run the storage assessment

### Preflight only

```bash
python -m cloudmark run storage --profile disk-quick
```

Without `--yes`, CloudMark checks only:

- whether `fio` exists;
- whether the workspace path is valid;
- available free space;
- the required safety reserve;
- the temporary file size.

### Execute the profile

```bash
python -m cloudmark run storage --profile disk-quick --yes
```

Or open **Storage Assessment**, select a profile, and select **Run assessment**.

The default profile uses:

- a 512 MiB file under `.cloudmark/benchmark-workspace`;
- sequential read and write;
- random 4 KiB QD1;
- mixed 70/30;
- P50/P90/P95/P99/P99.9;
- cleanup after successful completion or failure.

Run the standard profile with:

```bash
python -m cloudmark run storage --profile disk-standard --yes
```

The standard profile uses a 4 GiB temporary file and runs longer. Do not run it
on a production system carrying active workloads when the result will be used
for provider comparison.

Additional profiles:

- `disk-database`: 2 GiB, database-oriented 8 KiB latency and fsync workloads;
- `disk-throughput`: 4 GiB, large-block scaling for backup, media, and analytics;
- `disk-sustained`: 8 GiB, long mixed phases for burst-credit and throttling detection.

The Storage page displays the current phase, job, completed steps, percentage,
and a **Cancel run** control. Cancellation stops fio, removes temporary files,
and retains already completed jobs as partial evidence. Cancelled results are
never treated as a completed assessment.

### Operations CloudMark does not perform

- write to `/dev/sda`, `/dev/nvme0n1`, or a raw Windows disk;
- format a volume;
- run TRIM or discard;
- precondition the entire device;
- cut power to test power-loss protection.

## 10. Create a multi-system session

Open **Distributed Testing** and select **Create pairing session**. CloudMark
creates a session ID, join token, and 30-minute expiry.

After upgrading from 0.2, create a new session. Earlier registrations do not
have the per-agent credentials or advertised peer address required by 0.3 and
newer releases.

Bootstrap the network pack on both VMs:

```bash
sudo python -m cloudmark bootstrap --packs network --yes
```

On VM A, keep this process running:

```bash
python -m cloudmark agent \
  --controller https://CONTROLLER \
  --session SESSION_ID \
  --token JOIN_TOKEN \
  --role target \
  --advertise-address VM_A_PEER_IP
```

On VM B, keep this process running:

```bash
python -m cloudmark agent \
  --controller https://CONTROLLER \
  --session SESSION_ID \
  --token JOIN_TOKEN \
  --role generator \
  --advertise-address VM_B_PEER_IP
```

If the Controller is available only through HTTP inside a trusted VPN or
private network, add `--allow-http`. Never use that option over the public
Internet.

When both workers are online and report `iperf3`, select `Provider Peer Quick`
or `Provider Internal Network`, then select **Run network assessment**. The
quick profile runs 1- and 4-stream TCP in both directions. The standard profile
runs bounded idle latency, 1/4/8/16-stream TCP in both directions, adaptive UDP
loss and jitter sweeps at 25/50/90% of the measured directional TCP peak, and a
simultaneous bidirectional TCP measurement. The Controller never becomes a
performance endpoint.

The standard Network v7 profile also requires `iproute2`, `tracepath`, `ethtool`, and Linux
TCP congestion-control evidence on both Agents. The dashboard keeps the run
button disabled until both refreshed Agent inventories report those
capabilities. The Quick profile requires only `iperf3`.

The executor accepts only paired-agent addresses, ports 5201–5210, durations up
to 60 seconds, an allow-list of stream counts, capped UDP rates, and bounded
ping parameters. Each server is one-shot and has an independent watchdog
deadline. Linux network-v7 runs capture pre/post route, bounded numeric path traces, and structured aggregate interface
byte/packet/error/drop counters, egress-interface, interface-MTU, NIC driver,
selected offload states, active TCP congestion-control evidence, and path MTU
when exposed by `tracepath`. These are fixed read-only queries against the
route-derived interface. Counter deltas cover all traffic on that interface
during the Run, so CloudMark reports this scope explicitly. Observed drops and
errors remain evidence; they do not make a poor result disappear. Network v7
also records bounded common driver per-queue counters from `ethtool -S`, showing
active queue distribution and busiest-queue share when supported. Those names
vary by NIC, so missing per-queue evidence remains observational rather than a
failure. CloudMark rejects v7 comparison evidence when pre/post route stability, destination-reaching
bounded traces, NIC/TCP-control/counter evidence, or Generator CPU and scaling
headroom is insufficient. Address class and observed hops do not prove public
Internet transit.

For time-separated evidence, keep `Provider Internal Network` selected and use
**Create 3-day campaign**. The campaign permanently binds the current Target,
Generator, topology evidence class, standard profile version, and Network v7
methodology. Select **Run next campaign window** once in each authorized UTC
day. CloudMark never schedules these Runs silently, failed attempts can be
retried, and no more than one comparison-eligible Run counts per UTC day. The
dashboard shows valid windows, attempts, failures, and the exact reason the next
window is available or blocked. A profile upgrade supersedes an unfinished
campaign without deleting its existing Runs. Completing this campaign describes one fixed
pair across time; provider comparison still requires independent targets.

Overall network coverage remains `Partial`
because unattended campaign scheduling, physical-fabric verification, wider
per-queue NIC normalization and queue/interrupt affinity, administrative path verification, Windows route
parity, and mTLS Agent enrollment are not complete. Session topology declarations are already
checked against trusted region/zone metadata when those facts are available;
the dashboard keeps claims and independent observations separate. Public IP
address class is not treated as proof of public-Internet traversal.

## 11. Run a PostgreSQL peer assessment

Use the same Target and Generator roles as the network topology. Install the
database pack on both machines, then restart both Agents so their capabilities
are refreshed:

```bash
sudo python -m cloudmark bootstrap --packs database --yes
```

Open **Database Assessment**, select `PostgreSQL Peer Quick` or `PostgreSQL
Peer Standard`, choose the paired session, and select **Run database
assessment**. CloudMark creates a temporary PostgreSQL cluster beneath the
Target Agent workspace, initializes a fixed pgbench dataset, runs built-in
workloads from the Generator, stops PostgreSQL, and verifies removal of the
cluster.

Allow TCP port `55432` only from the Generator peer address to the Target. Run
the Target Agent as a non-root account; PostgreSQL initialization refuses root.
Do not reuse the temporary CloudMark cluster for application data. The profile
keeps `fsync`, full-page writes, and synchronous commit enabled so the result is
not an unsafe durability-off headline number.

The dashboard reports TPS, average latency, failed transactions, concurrency,
dataset scale, tool versions, and cleanup status. PostgreSQL v1 marks
transaction P95/P99 latency unavailable rather than estimating it from
one-second averages. See [`DATABASE_METHODOLOGY.md`](DATABASE_METHODOLOGY.md).

### Dispatch a single-system profile to an Agent

After an Agent is online, open **Compute & Memory** or **Storage Assessment**
and choose it under **Execution target**. The capability indicators use that
Agent's inventory rather than the Controller inventory. The Agent workspace is
configured locally with `cloudmark agent --workspace`; the Controller cannot
choose an arbitrary remote path.

The Agent reports progress every second and polls cancellation while a child
process runs. It cancels load after more than 20 seconds without Controller
contact. The Controller fails a task after a 45-second task-heartbeat gap. See
[`REMOTE_EXECUTION.md`](REMOTE_EXECUTION.md) for the complete protocol and
safety contract.

## 12. Run a Web/API/TLS peer assessment

Use the same Target and Generator roles as the network and database topology.
Install the web pack on both machines, then restart both Agents so their
capabilities are refreshed:

```bash
sudo python -m cloudmark bootstrap --packs web --yes
```

Open **Web & API Assessment**, select `Web & TLS Peer Quick` or `Web & TLS
Peer Standard`, choose the paired session, and select **Run Web/API/TLS
assessment**. CloudMark creates fixed payloads and an ephemeral certificate
beneath the Target Agent workspace, starts isolated Nginx listeners, dispatches
bounded ApacheBench jobs from the Generator, then stops Nginx and verifies
removal of the complete service directory.

Allow TCP ports `58080` and `58443` only from the Generator peer address to the
Target. Run the Target Agent as a non-root account. The API accepts only fixed
CloudMark endpoints and never accepts an arbitrary target URL. DDoS testing is
outside this methodology.

The dashboard reports request throughput, error counts, success percentage,
P50/P90/P95/P99/maximum latency, transfer rate, TLS protocol evidence, tool
versions, and cleanup status. Compare only equivalent Target and Generator
classes and verify Generator headroom because ApacheBench may become the
bottleneck. See [`WEB_METHODOLOGY.md`](WEB_METHODOLOGY.md).

## 13. Workload suitability

Open **Workload Suitability**, then select the exact observed Target and one
requirement level:

- **Essential:** entry production or light-duty baseline;
- **Standard:** general production baseline;
- **Demanding:** higher sustained throughput, concurrency, and tighter latency.

Each of the 12 use cases reports `Insufficient evidence`, `Below requirement`,
`Conditional fit`, or `Suitable`. Select a use case to inspect every hard gate,
observed value, threshold, evidence state, Run ID, profile, methodology,
blocker, limitation, and recommended next assessment. Evidence older than 30
days is shown as stale and cannot satisfy a current gate.

Coverage and the pass ratio among measured checks are intentionally separate.
For example, passing every available check with only 30% coverage remains
`Insufficient evidence`. A target that passes all current metrics but still
lacks a required product capability is at most `Conditional fit`.

The provider panel remains **Not rated** for one VM or one time window. It shows
the same-SKU target count, measurement-window count, observed suites, and the
missing security, reliability, control-plane, and cost gates. See
[`SUITABILITY_METHODOLOGY.md`](SUITABILITY_METHODOLOGY.md).

## 14. Provider comparison

Open **Provider Comparison** and select an exact metric contract. A contract is
one metric, profile, methodology, and unit. The dashboard then shows each
provider/SKU/region/OS cohort without merging incompatible evidence.

Each cohort shows median, P10/P90, actual worst value, stability, Run count,
target count, and UTC-day count. `Observation only` means the evidence remains
useful but has not reached the minimum nine Runs, three targets, and three
UTC-day windows. `Comparable` means only that the sampling contract is met; it
does not mean the provider is recommended or ranked above another provider.

Use the same official profile on three independent provider instances and
repeat it on three different UTC dates. Do not create nested VMs on one target
to inflate the target count. Network evidence must come from paired provider
Agents, and CloudMark counts one paired Run once.

## 15. API quick reference

Health:

```bash
curl http://127.0.0.1:8787/api/v1/health
```

System evidence:

```bash
curl http://127.0.0.1:8787/api/v1/system
```

Suitability evidence:

```bash
curl http://127.0.0.1:8787/api/v1/suitability
```

Repeated-window provider observations:

```bash
curl http://127.0.0.1:8787/api/v1/provider-comparisons
```

Create an inventory run:

```bash
curl -X POST http://127.0.0.1:8787/api/v1/runs \
  -H "Content-Type: application/json" \
  -H "X-CloudMark-Token: TOKEN" \
  -d '{"suite":"inventory","profile":"default"}'
```

## 15. Local data

```text
.cloudmark/
├── cloudmark.sqlite3
├── cloudmark.sqlite3-wal
├── cloudmark.sqlite3-shm
├── controller.token
└── benchmark-workspace/
```

The complete directory is excluded by `.gitignore`.

## 16. Recommended provider-assessment procedure

1. Create two clean VMs with the same SKU, OS, and disk type.
2. Use anti-affinity when possible so the VMs do not share a physical host.
3. Bootstrap the same CloudMark and tool versions.
4. Collect inventory on both systems.
5. Select each Agent and run compute, memory, and storage profiles separately.
6. Run saturation profiles concurrently only when intentionally measuring contention.
7. Pair A and B for network, web, and database client/server tests.
8. Create fresh instances and repeat in different time windows.
9. Never generalize one VM or one run to the complete provider.

## 17. Troubleshooting

### Dashboard reports API offline

- confirm that `cloudmark serve` is still running;
- open `http://127.0.0.1:8787/api/v1/health`;
- check port 8787;
- dashboard development origins are limited to localhost ports 3000–3010.

### Storage reports missing fio

Run `doctor`, then run `bootstrap --packs storage --yes` with sudo or root.

### Compute or memory preflight fails

Run `doctor --packs compute,memory`. CPU assessment requires sysbench 1.0 or
newer. Memory assessment requires Linux, GCC, and OpenMP, and refuses to start
when its fixed allocation would violate the 512 MiB available-memory reserve.

### Insufficient free space

Select another filesystem with `--workspace`. Never remove or reduce the safety reserve.

### Provider is Unknown

Metadata may be disabled, blocked by a firewall, or unsupported by the provider.
CloudMark does not use ASN data to assert provider identity. Regional provider
packs and signed self-hosted manifests are planned.

### An Agent cannot join the Controller

The Controller binds to loopback by default, so remote VMs cannot reach it.
For remote agents, use a VPN or an operator-controlled HTTPS reverse proxy to
the Controller. Confirm that each VM can reach the other VM's advertised IP on
TCP 5201–5210. Never use `--allow-http` over the public Internet. mTLS and relay
enrollment are roadmap security layers; version 0.5 uses per-agent bearer
credentials and requires HTTPS for remote control connections by default.

### A remote benchmark stops or never starts

- verify the selected Agent remains `online` and the same worker process is running;
- install the required pack on that Agent and restart it to refresh inventory;
- confirm Controller HTTPS/VPN reachability in both directions during the run;
- do not run a peer-network assessment and a saturation profile in the same
  Agent session at the same time;
- inspect the run error and retained partial result in **History**.

### A network run remains queued or times out

- keep both `cloudmark agent` processes running;
- verify the dashboard shows one online target and one online generator;
- install `iperf3` on both VMs;
- allow TCP and UDP 5201–5210 between the two provider VMs only, plus ICMP when
  the standard profile's idle-latency evidence is required;
- verify `--advertise-address` is reachable from the peer, not a loopback or
  management address hidden behind NAT;
- do not expose the iperf3 port range to the public Internet.

## 18. Validate the project

Python tests:

```bash
python -m unittest discover -s tests_python -v
```

Dashboard production build:

```bash
pnpm run build
```

Never run CPU, memory, or full storage benchmarks in shared CI environments.
