# Network methodology

CloudMark does not benchmark from provider machines back to the operator's
Controller. Performance traffic is direct between authenticated agents in the
provider environment; the Controller carries scheduling and result traffic
only.

## Version 0.3 topology

- Target agent: provider VM A.
- Generator agent: provider VM B.
- Controller: orchestration and evidence storage only.

Both agents run persistent workers. Each worker receives an independent random
credential after joining a 30-minute session. Credentials are stored by the
Controller only as SHA-256 hashes. Remote control connections require HTTPS by
default; `--allow-http` is an explicit exception for an isolated management
network or trusted VPN.

## Available TCP profiles

| Profile | Direction | Streams | Duration per measurement |
|---|---|---:|---:|
| `network-peer-quick` | A→B and B→A | 1, 4 | 10 seconds |
| `network-peer-standard` | A→B and B→A | 1, 4, 8, 16 | 15 seconds |

Each direction and stream count is stored independently. CloudMark records
sender and receiver throughput, transferred bytes, retransmissions, and local
and remote CPU values when the installed iperf3 version exposes them. Raw
iperf3 JSON is retained with normalized metrics.

CloudMark starts an iperf3 one-shot server immediately before each measurement
and collects it immediately afterward. The agent watchdog stops abandoned
servers. TCP ports are restricted to `5201–5210`, duration is capped at 60
seconds, parallel streams are restricted to `1`, `4`, `8`, or `16`, arbitrary
shell commands are never accepted, and the peer address comes only from the
paired agent record.

## Not yet available

The network domain remains `Partial`. Version 0.3 does not yet claim:

- UDP rate sweeps, loss, jitter, or reorder;
- idle-versus-loaded latency and bufferbloat;
- simultaneous bidirectional iperf3 mode;
- MTU/route evidence or generator-saturation validity checks;
- mTLS agent identity or relay-based enrollment;
- public-Internet security for an HTTP Controller endpoint.

These missing measurements do not receive a zero and do not influence provider
or workload scoring.

## Validity

Two VMs on the same physical host are valid for virtual-switch and hypervisor
tests, not for provider-fabric claims. Provider-grade results should use
anti-affinity or placement evidence, identical instance shapes, matching tool
versions, fresh instances, and repeated time windows. A complete comparison
must preserve each direction rather than averaging A→B and B→A.
