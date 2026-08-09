# CloudMark user guide

This guide applies to version `0.2.0`. The current release provides system
inventory, AWS/Azure/Google Cloud metadata detection, tool bootstrap planning,
filesystem-safe storage assessment through `fio`, SQLite history, multi-system
topology registration, and a local dashboard. The dashboard distinguishes
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
Controller/dashboard + Agent on the system under assessment
```

Use this model for inventory, provider detection, CPU/memory evidence, GPU
inventory, and local or block storage.

### Assess a provider using multiple VMs

```text
Operator system: Controller + dashboard
Provider:       VM A (target) ↔ VM B (generator)
Optional:       VM C (replica/failover)
```

The Controller never receives cloud benchmark traffic. Network benchmark data
flows directly between VM A and VM B.

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
- **Storage Assessment** provides Quick, Standard, Database, Throughput, and
  Sustained profiles with live progress and cancellation.
- **Distributed Testing** creates a multi-agent topology. Network traffic
  execution remains `Partial` until all safety guards are complete.
- **Workload Suitability** maps technical evidence to 12 use cases. Missing
  required metrics return `Insufficient evidence`, not zero.
- **History** retains raw results so conclusions can be recalculated when the
  methodology changes.

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
python -m cloudmark doctor --packs storage,network,database,web
```

This command displays a plan and does not modify the system.

| Pack | Contents |
|---|---|
| `base` | curl, jq, dmidecode, sysstat, numactl |
| `storage` | fio, smartmontools, nvme-cli |
| `network` | iperf3, ethtool, mtr, DNS tools |
| `database` | sysbench, PostgreSQL, Redis |
| `web` | nginx and HTTP utilities |

## 7. Bootstrap tools

### Ubuntu or Debian

```bash
sudo python -m cloudmark bootstrap \
  --packs storage,network,database,web \
  --yes
```

### RHEL or CentOS

CloudMark detects `dnf` or `yum` automatically:

```bash
sudo python -m cloudmark bootstrap --packs storage,network,database,web --yes
```

Some packages such as `sysbench` may require an additional repository. If the
package manager rejects the operation, bootstrap stops and preserves the error.

### SLES 12.5 or 15

```bash
sudo python -m cloudmark bootstrap --packs storage,network,database,web --yes
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

## 8. Run the storage assessment

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

## 9. Create a multi-system session

Open **Distributed Testing** and select **Create pairing session**. CloudMark
creates a session ID, join token, and 30-minute expiry.

On VM A:

```bash
python -m cloudmark join \
  --controller https://CONTROLLER \
  --session SESSION_ID \
  --token JOIN_TOKEN \
  --role target
```

On VM B:

```bash
python -m cloudmark join \
  --controller https://CONTROLLER \
  --session SESSION_ID \
  --token JOIN_TOKEN \
  --role generator
```

If the Controller is available only through HTTP inside a trusted VPN or
private network, add `--allow-http`. Never use that option over the public
Internet.

Agent registration and inventory persistence are operational. Automatic direct
`iperf3` execution between A and B is not enabled yet, so dashboard coverage is
`Partial`. This restriction prevents exposing a load-generating network endpoint
before mTLS, watchdog, and rate limits are complete.

## 10. Workload suitability

The 12 use cases use three coverage states:

- `Available`: an executor and profile can run now;
- `Partial`: only part of the required evidence or topology is available;
- `Roadmap`: no executor exists yet, and no artificial zero is assigned.

A suitability conclusion appears only after all mandatory raw evidence is
available. See the assessment catalog for each use case's hard gates.

## 11. API quick reference

Health:

```bash
curl http://127.0.0.1:8787/api/v1/health
```

System evidence:

```bash
curl http://127.0.0.1:8787/api/v1/system
```

Create an inventory run:

```bash
curl -X POST http://127.0.0.1:8787/api/v1/runs \
  -H "Content-Type: application/json" \
  -H "X-CloudMark-Token: TOKEN" \
  -d '{"suite":"inventory","profile":"default"}'
```

## 12. Local data

```text
.cloudmark/
├── cloudmark.sqlite3
├── cloudmark.sqlite3-wal
├── cloudmark.sqlite3-shm
├── controller.token
└── benchmark-workspace/
```

The complete directory is excluded by `.gitignore`.

## 13. Recommended provider-assessment procedure

1. Create two clean VMs with the same SKU, OS, and disk type.
2. Use anti-affinity when possible so the VMs do not share a physical host.
3. Bootstrap the same CloudMark and tool versions.
4. Collect inventory on both systems.
5. Run storage profiles on each VM separately.
6. Run them concurrently only when intentionally measuring contention.
7. Pair A and B for network, web, and database client/server tests.
8. Create fresh instances and repeat in different time windows.
9. Never generalize one VM or one run to the complete provider.

## 14. Troubleshooting

### Dashboard reports API offline

- confirm that `cloudmark serve` is still running;
- open `http://127.0.0.1:8787/api/v1/health`;
- check port 8787;
- dashboard development origins are limited to localhost ports 3000–3010.

### Storage reports missing fio

Run `doctor`, then run `bootstrap --packs storage --yes` with sudo or root.

### Insufficient free space

Select another filesystem with `--workspace`. Never remove or reduce the safety reserve.

### Provider is Unknown

Metadata may be disabled, blocked by a firewall, or unsupported by the provider.
CloudMark does not use ASN data to assert provider identity. Regional provider
packs and signed self-hosted manifests are planned.

### An Agent cannot join the Controller

The Controller binds to loopback by default, so remote VMs cannot reach it.
For remote registration, use a VPN or an operator-controlled HTTPS reverse
proxy. Never use `--allow-http` over the public Internet. Outbound relay and
mTLS enrollment must be complete before automated network execution is enabled.

## 15. Validate the project

Python tests:

```bash
python -m unittest discover -s tests_python -v
```

Dashboard production build:

```bash
pnpm run build
```

Never run full storage benchmarks in shared CI environments.
