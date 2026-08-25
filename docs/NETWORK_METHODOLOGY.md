# Network methodology

CloudMark never uses the Controller as a provider-throughput endpoint. Network
performance traffic flows directly between an authenticated target Agent and
generator Agent. The Controller carries orchestration, progress, and evidence
only.

## Topology and evidence identity

- Agent A: the assessed target in the provider environment.
- Agent B: the workload generator in the same declared test topology.
- Controller: coordination and evidence storage only.

Each result identifies the profile and methodology version, both Agents, each
advertised peer address, direction, tool version, duration, and raw tool output.
The operator must record whether the pair is same-host, same-zone, cross-zone,
or cross-region. CloudMark does not infer fabric scope from throughput alone.

Once both Agents join, CloudMark independently derives a placement scope when
trusted provider metadata identifies both regions/zones. The declaration is
marked confirmed, compatible, contradicted, or unavailable. Globally routable
advertised peer endpoints are address-class evidence only; they do not prove
public-Internet traversal. This does not prove same-host placement or the
physical provider fabric.

## Profiles

| Profile | Methodology | Measurements |
|---|---|---|
| `network-peer-quick` | `network-v1` | TCP A→B and B→A, 1 and 4 streams, 10 seconds each |
| `network-peer-standard` | `network-v9` | Pre/post route, bounded path-trace, aggregate interface-counter, and driver per-queue snapshots; route/interface/MTU, NIC driver/offload, TCP congestion-control, system-resolver, and guest RSS/RPS/XPS/MSI IRQ-affinity evidence; idle latency; TCP scaling; adaptive UDP sweeps; simultaneous bidirectional TCP; and Generator headroom validation |

The standard profile contains 21 evidence steps:

1. allow-listed pre-load route, egress-interface, structured aggregate and driver per-queue counter,
   interface-MTU, path-MTU, bounded numeric path trace, NIC driver/offload, active TCP
   congestion-control, fixed system-resolver diagnostics, and bounded guest-visible
   RSS/RPS/XPS/MSI IRQ-affinity observations on both Agents;
2. bounded idle ICMP latency in both directions: 20 probes at 100 ms intervals;
3. TCP A→B and B→A at 1, 4, 8, and 16 streams for 15 seconds;
4. UDP A→B and B→A at 25%, 50%, and 90% of that direction's measured peak TCP receiver rate for 15 seconds; and
5. one simultaneous bidirectional TCP test with four streams for 15 seconds;
   and
6. matching post-load route, bounded path-trace, NIC, and structured aggregate/per-queue counter snapshots in
   both directions.

UDP targets are rounded to 1 Mbit/s and clamped between 1 Mbit/s and 1 Gbit/s.
The per-Agent executor has an independent absolute UDP allow-list of 100 kbit/s
to 1 Gbit/s, so a modified Controller cannot request an unbounded rate.

## Metrics

TCP measurements preserve sender and receiver throughput, transferred bytes,
retransmissions, CPU utilization when exposed by iperf3, and TCP_INFO RTT from
sender stream data. UDP measurements preserve requested and achieved rate,
jitter, packet count, loss count and percentage, and out-of-order packets when
the installed iperf3 exposes them. Simultaneous bidirectional results preserve
the forward and reverse receiver rates separately.

The latency analysis compares idle ICMP average latency with the TCP_INFO mean
RTT observed during the highest-stream throughput measurement. These are
different protocols and sampling mechanisms. CloudMark reports the absolute and
percentage change as diagnostic evidence but deliberately does not assign a
bufferbloat score.

`network-v9` evaluates Generator headroom independently in each
direction. The iperf3 client is the sender, so CloudMark selects local or
remote CPU evidence according to which endpoint has the `generator` role. A
direction is constrained when Generator CPU reaches 90%, or when throughput
scaling stalls below 5% while Generator CPU is at least 85%. Missing CPU data
produces `unknown`, not a passing result. Network-v9 evidence is eligible for
suitability and provider comparison only when both route/interface/MTU probes,
destination-reaching bounded traces, NIC driver/offload observations, and TCP
congestion-control observations are complete, the route interface/gateway/source
is stable across both boundaries, the pre/post interface-counter window is
complete, and Generator headroom is adequate in every measured direction.

On Linux, route identity uses JSON output from fixed `ip route get` and `ip
link show` commands. CloudMark runs numeric `tracepath` toward only the paired
address with a maximum of eight hops. It records at most one normalized
observation per hop, whether the exact destination was reached, each numeric
address class, RTT when exposed, and path MTU. Interface MTU and path MTU are
kept separate because they are not interchangeable. Trace-sequence changes are
retained but are not a failure by themselves because ECMP can legitimately
change observed hops. Address class and observed hops never prove administrative
ownership or public-Internet transit. Windows Agents currently report this
evidence as unavailable.

On the exact Linux egress interface discovered by that route lookup, CloudMark
runs fixed read-only `ethtool -i` and `ethtool -k` queries. It retains bounded
driver identity and selected checksum, segmentation, aggregation, VLAN, hash,
and filter feature states. The active TCP congestion-control algorithm is read
from Linux procfs. CloudMark does not enable or disable offloads, change the
congestion-control algorithm, or accept an interface name from the Controller.
Missing tools or unsupported virtual NIC features remain `unavailable`; they
are not converted to zero. Network-v2 through network-v8 results remain
readable under their original contracts and do not gain v9 claims.

Each pre/post snapshot retains cumulative RX/TX bytes, packets, errors, and
drops from structured `ip -s -j link` output. CloudMark computes a delta only
when the route-derived interface remains identical, every counter is available,
and no counter decreases. A decrease can indicate reset, interface recreation,
or unsupported counter behavior and makes the counter window unavailable. The
delta includes all traffic on that interface during the Run, including bounded
Agent control traffic when data and control share one NIC. Observed drops and
errors remain comparable measured evidence; CloudMark never rejects a poor
result merely because those values are non-zero.

Network v9 also executes fixed read-only `ethtool -S` against that same
route-derived interface at both boundaries. Because driver statistic names are
not standardized, CloudMark recognizes only a bounded set of common queue
counter shapes, limits queue indexes to 0-127, and examines at most 4,096 lines
per snapshot. It reports active RX/TX queues, the busiest queue share, and
driver-exposed queue drop/error deltas. Unknown vendor counters remain
unclassified, queue-set or counter resets remain partial, and absent queue
support remains unavailable. Per-queue evidence is observational and is not a
comparison gate; making vendor-specific availability a hard requirement would
systematically exclude otherwise valid NICs and clouds.

At the pre-load boundary, Network v9 reads at most 64 KiB of Linux
`/etc/resolv.conf`, persists configured nameserver address/family/class,
redacts search-domain names while retaining their count, and accepts only a
small allow-list of resolver options. When `dig` is installed, each Agent makes
exactly one A and one AAAA query for the fixed IANA-reserved `example.com.`
name, with one try, a two-second DNS timeout, and a five-second process
watchdog. CloudMark retains the response status, elapsed wall time, answer
count, and answer address classes but not returned addresses. The Controller
cannot supply a query name or record type.

This resolver evidence is diagnostic and observational. A system resolver may
be a local stub backed by a cache, split-DNS policy, or an upstream resolver
that CloudMark cannot identify. Therefore a single lookup never becomes a
provider DNS latency or availability claim, never attributes the upstream
service to the provider, and never gates network comparison eligibility.
Missing `dig` produces configuration-only partial evidence rather than a zero
or a rejected throughput Run. Windows resolver parity is not implemented.

At that same pre-load boundary, Network v9 reads guest-visible queue placement
only for the route-derived Linux interface. A fixed read-only `ethtool -x`
query records at most 4,096 RSS indirection entries across queue indexes 0-127,
the active queue distribution, and the enabled hash-function name. The RSS hash
key is deliberately never persisted. Linux sysfs contributes bounded RPS and
XPS CPU masks for at most 128 RX/TX queues; procfs contributes affinity lists
for at most 256 MSI IRQs exposed by that interface. Every control file is capped
at 4,096 bytes.

RSS, RPS, XPS, and IRQ-affinity evidence is observational. A virtual NIC may
hide some or all controls, and guest-visible placement cannot establish the
physical host NIC, hardware RSS table, interrupt isolation, or provider fabric.
Missing support therefore remains `unavailable` rather than zero and never
invalidates an otherwise comparison-eligible Network v9 Run. CloudMark does
not run `ethtool -X`, write a sysfs/procfs file, or change NIC/kernel settings.

## Safety gates

- peer destinations come only from the paired Agent records;
- loopback, unspecified, multicast, and link-local destinations are rejected;
- iperf3 ports are restricted to `5201–5210`;
- durations are clamped to 1–60 seconds;
- stream counts are restricted to `1`, `4`, `8`, or `16`;
- guarded UDP uses exactly one stream and a capped numeric bit rate;
- ping count, interval, and timeout are bounded;
- iperf3 servers are one-shot and have watchdog deadlines;
- route, MTU, bounded path-trace, NIC, aggregate/per-queue counter, and TCP-control probes use only the paired peer IP, the
  route-derived egress interface, fixed arguments, and read-only kernel state;
- resolver queries use only the fixed `example.com.` name and A/AAAA record types,
  and search-domain names and returned answer addresses are not persisted;
- guest queue placement is bounded to 128 queues, 4,096 RSS entries, and 256
  MSI IRQs; the RSS hash key is not persisted and no steering or affinity value
  is written;
- arbitrary commands and raw shell input are never accepted; and
- only one saturation suite may target an Agent at a time.

The standard profile can saturate a provider link. It requires explicit
`confirm_network_load` authorization and may incur provider egress or traffic
charges, particularly across zones or regions.

The Controller refuses to start Network v9 unless both Agents advertise
`iperf3`, `iproute2`, `tracepath`, `ethtool`, and Linux TCP congestion-control
capabilities. This prevents an expensive load run that can never satisfy the
v9 comparison contract. `dig` is installed by the Network bootstrap pack and
reported as a capability, but remains optional because resolver evidence is
observational.

## Repeated campaign acquisition

`network-campaign-v1` binds one fixed Target/Generator pair, topology evidence
class, standard profile version, and Network v9 methodology for 3-30 distinct
UTC-day windows. Creating the campaign does not generate traffic. The operator
must explicitly confirm each window, and CloudMark counts at most one completed
comparison-eligible Run per UTC day. Failed and cancelled attempts remain
visible and may be retried without becoming valid windows. Campaign completion
is temporal evidence for one pair; provider-level comparison still requires
independent targets in the same exact cohort. An incomplete active campaign
becomes `superseded` when the installed standard profile or methodology changes;
CloudMark preserves its Runs but requires a new immutable campaign contract.

## Validity and remaining limitations

Two VMs on the same physical host measure the virtual switch and hypervisor,
not necessarily the provider fabric. Provider-grade comparisons should use
identical shapes, placement or anti-affinity evidence, matching tool versions,
fresh instances, several time windows, and preserved directional results.

The network domain remains `Partial`. Driver-exposed per-queue NIC counters are
observational and not yet normalized across every NIC family. Guest-visible
steering and IRQ affinity do not verify physical-host placement. System-resolver
diagnostics do not yet cover controlled authoritative DNS, repeated cache-cold
resolution, DNSSEC, TCP fallback, or Windows. CloudMark does not yet provide
administrative route-ownership verification,
unattended campaign scheduling, cross-pair orchestration, physical-fabric
verification, or mTLS Agent identity. Windows
latency parsing currently supports English `ping` output, while Windows route
and MTU evidence is unavailable. Missing evidence is never converted to a zero
score.
