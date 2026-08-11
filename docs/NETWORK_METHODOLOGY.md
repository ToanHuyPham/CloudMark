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

## Profiles

| Profile | Methodology | Measurements |
|---|---|---|
| `network-peer-quick` | `network-v1` | TCP A→B and B→A, 1 and 4 streams, 10 seconds each |
| `network-peer-standard` | `network-v2` | Idle latency, TCP scaling, adaptive UDP sweeps, and simultaneous bidirectional TCP |

The standard profile contains 17 measurements:

1. bounded idle ICMP latency in both directions: 20 probes at 100 ms intervals;
2. TCP A→B and B→A at 1, 4, 8, and 16 streams for 15 seconds;
3. UDP A→B and B→A at 25%, 50%, and 90% of that direction's measured peak TCP receiver rate for 15 seconds; and
4. one simultaneous bidirectional TCP test with four streams for 15 seconds.

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

## Safety gates

- peer destinations come only from the paired Agent records;
- loopback, unspecified, multicast, and link-local destinations are rejected;
- iperf3 ports are restricted to `5201–5210`;
- durations are clamped to 1–60 seconds;
- stream counts are restricted to `1`, `4`, `8`, or `16`;
- guarded UDP uses exactly one stream and a capped numeric bit rate;
- ping count, interval, and timeout are bounded;
- iperf3 servers are one-shot and have watchdog deadlines;
- arbitrary commands and raw shell input are never accepted; and
- only one saturation suite may target an Agent at a time.

The standard profile can saturate a provider link. It requires explicit
`confirm_network_load` authorization and may incur provider egress or traffic
charges, particularly across zones or regions.

## Validity and remaining limitations

Two VMs on the same physical host measure the virtual switch and hypervisor,
not necessarily the provider fabric. Provider-grade comparisons should use
identical shapes, placement or anti-affinity evidence, matching tool versions,
fresh instances, several time windows, and preserved directional results.

The network domain remains `Partial`. CloudMark does not yet provide automatic
route/MTU/offload capture, generator-saturation rejection, DNS/IPv6 coverage
classification, repeated-window aggregation, topology verification, or mTLS
Agent identity. Windows latency parsing currently supports English `ping`
output; other localized summaries are rejected instead of guessed. Missing
evidence is never converted to a zero score.
