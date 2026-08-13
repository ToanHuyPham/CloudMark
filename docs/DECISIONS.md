# CloudMark architectural decisions

This document records decisions that must survive individual chats.

## D-001: Evidence is not a synthetic score

**Decision:** Store measured evidence and provenance first. A score may be
computed only when its required evidence exists. Partial and Roadmap domains do
not receive zero.

**Reason:** Zero incorrectly means measured failure, while missing evidence
means the assessment is incomplete.

## D-002: The Controller is not a provider-network endpoint

**Decision:** Do not use operator-to-provider bandwidth as the provider's
internal network score. Provider throughput tests run directly between paired
provider Agents.

**Reason:** The operator ISP, geography, routing, NAT, firewall, and home or
office hardware would dominate the result.

## D-003: Multi-machine provider assessment

**Decision:** Use one target Agent and at least one peer/generator Agent for
provider network and client/server workloads. Add a third system for
cross-zone, replica, or failover scenarios when the methodology requires it.

**Reason:** Creating nested VMs on one physical or virtual target measures the
same host and virtual switch more than it measures the provider network.

## D-004: Filesystem-safe storage by default

**Decision:** Public profiles operate on temporary files, never raw devices,
and preserve a free-space reserve. Cleanup is part of the result contract.

**Reason:** CloudMark must be safe on newly provisioned systems and must not
silently become a destructive disk qualification tool.

## D-005: Multiple storage profiles

**Decision:** Maintain separate Quick, Standard, Database, Throughput, and
Sustained profiles with latency percentiles and one-second evidence where
applicable.

**Reason:** Sequential bandwidth alone cannot characterize database latency,
queue scaling, synchronous writes, burst credits, or sustained throttling.

## D-006: Explicit execution target

**Decision:** Dashboard-triggered saturation suites execute only on the target
selected by the operator. Results retain Controller-versus-Agent attribution
and target inventory.

**Reason:** A dashboard on the operator machine must not accidentally benchmark
that machine when the operator intends to evaluate a provider VM.

## D-007: English public repository

**Decision:** Source, UI, public documentation, examples, and commit messages
are English. Operator conversations may use the operator's preferred language.

**Reason:** CloudMark is intended for public production use and collaboration,
while operational support should remain accessible to the operator.

## D-008: Secrets remain outside Git

**Decision:** `.cloudmark/`, tokens, private keys, provider credentials, and
active target addresses stay outside tracked files. Recovery uses a separate
protected runtime snapshot.

**Reason:** The repository is public and Git history is not an appropriate
secret store.

## D-009: Adaptive peer-network methodology

**Decision:** Preserve the quick directional TCP profile as `network-v1` and
use `network-v2` for bounded idle latency, directional TCP scaling, UDP targets
derived from each direction's measured TCP peak, and simultaneous
bidirectional TCP. Idle ICMP and loaded TCP_INFO RTT are reported together but
remain unscored because their protocols and sampling differ.

**Reason:** A fixed UDP rate can overload small VPS products or underexercise
large instances, while treating ICMP and TCP RTT as interchangeable would
create false precision. Independent Agent-side caps remain necessary even when
the Controller derives a guarded target.

## D-010: Ephemeral paired database baseline

**Decision:** The first database executor uses a fresh PostgreSQL cluster on a
Target Agent and built-in pgbench workloads from a paired Generator. Server
durability remains enabled, configuration is fixed by versioned profiles, and
the cluster is removed after the run. Transaction tail percentiles remain
unavailable until a reviewed sampling method is implemented.

**Reason:** A two-Agent service measures database, storage, CPU, and provider
network interaction without contaminating the Target with the load generator.
Fixed durable settings improve comparability, while refusing to invent missing
tail latency prevents a misleading provider score.

## D-011: Guarded Web/API/TLS paired baseline

**Decision:** The first web executor creates a fresh Nginx service on a Target
Agent and runs versioned ApacheBench jobs from a paired Generator. It accepts
only fixed addresses, ports, endpoints, payloads, concurrency levels, and TLS
1.2 behavior. The per-run certificate and complete service directory are
removed after the run. Arbitrary URLs and DDoS load are refused.

**Reason:** Separating the service and generator measures their provider path
without routing traffic through the Controller. A fixed workload makes results
auditable and limits accidental exposure, while explicit ApacheBench
limitations prevent the Generator from being mistaken for Target capacity.

## D-012: Gate-based target suitability before provider scoring

**Decision:** `suitability-v1` evaluates one explicit target against versioned
Essential, Standard, and Demanding hard gates. Each check retains run
provenance. Missing, stale, failed, incompatible, or cleanup-unverified
evidence cannot satisfy a gate and never becomes zero. Known unimplemented
capabilities cap a passing measured subset at `Conditional fit`. Provider
status remains `not-rated` until independent multi-target, multi-window,
security, reliability, control-plane, and cost gates exist.

**Reason:** Operators need actionable workload classification before every
future executor exists, but a synthetic percentage would hide missing evidence
and one VM cannot establish provider quality. Separate coverage, measured gate
results, limitations, and provider readiness preserve honest claims.

## D-013: Exact cohorts before provider comparison

**Decision:** Repeated-window statistics group only matching provider, SKU,
region, operating system, profile, methodology, metric, and unit. One UTC
calendar day is one window. A metric becomes comparable only after nine valid
fresh samples from three targets and three windows. Paired network Runs are
de-duplicated. Median, P10, P90, actual best/worst, and relative spread remain
descriptive; CloudMark does not rank providers.

**Reason:** Mixing regions, operating systems, profiles, or methodology
versions creates false precision. A small sample can still aid diagnosis but
must not look statistically equivalent to repeated independent evidence.

## D-014: Network path identity and Generator validity

**Decision:** Advance the standard peer profile to `network-v3`. Before load,
each Agent records its route, egress interface, interface MTU, and optional
bounded `tracepath` path MTU toward the exact paired address. After TCP scaling,
CloudMark evaluates the CPU headroom of the endpoint assigned the Generator
role in each direction. Incomplete route evidence, unavailable Generator CPU,
CPU at or above 90%, or stalled stream scaling near the CPU limit makes the run
ineligible for suitability and provider comparison. Legacy network-v2 results
remain readable but do not gain v3 validity claims retroactively.

**Reason:** Throughput without path identity can combine materially different
topologies, while a saturated load generator can understate provider capacity.
Separating interface MTU from observed path MTU and failing closed on missing
validity evidence prevents false precision.

## D-015: Paired topology is a comparison contract

**Decision:** Pairing sessions accept an explicit topology scope: same-host,
same-zone, cross-zone, cross-region, public-internet, or undeclared. The scope
is stored with the session and every paired network, database, and web result.
An `operator-declared` topology is accepted as a visibly labelled comparison
contract; an undeclared run remains usable for target diagnosis but is
observational in provider cohorts. Provider observation contracts include
topology so runs from different fabrics cannot be merged.

**Reason:** Provider SKU and region labels do not prove the path between two
instances. Treating topology as an explicit evidence field prevents a fast
same-zone result from being presented as cross-zone or public-path capacity.

## D-016: Topology claims and independent observations stay separate

**Decision:** After both Agents join, CloudMark derives a placement observation
only from trusted provider region/zone metadata. Globally routable advertised
peer endpoints are retained as address-class evidence but never prove
public-Internet traversal. The result records the operator declaration, the
independent observation, its source, and a status. Matching scopes are
confirmed; conflicting scopes are contradicted and remain observational.
Operator-declared and independently derived provider metrics use different
comparison contracts.

**Reason:** Smaller providers and self-operated clouds may have no metadata
service, so an operator claim must remain usable and visibly labelled. Where
independent facts do exist, silently ignoring a contradiction would allow
misclassified same-zone, cross-zone, cross-region, or public-Internet evidence.

## D-017: Read-only NIC and TCP-control evidence is part of Network v4 validity

**Decision:** Advance the standard peer profile to `network-v4`. On each Linux
Agent, CloudMark resolves the egress interface only from the fixed route lookup
to the paired peer, then records bounded `ethtool -i`, selected `ethtool -k`,
and active procfs TCP congestion-control evidence. These queries are read-only;
CloudMark never changes offloads or kernel network configuration. Complete
observations in both directions join route/MTU and Generator headroom as a
comparison-validity requirement. Unsupported platforms and missing tools remain
`unavailable`, not zero. Network-v3 evidence remains readable under its original
validity contract and does not gain Network v4 claims retroactively.

**Reason:** Throughput, loss, and latency can change materially with a virtual
NIC driver, checksum/segmentation/aggregation state, and congestion-control
algorithm. Capturing those facts makes repeated provider cohorts more
reproducible without changing the assessed machine or letting the Controller
select an arbitrary interface or command.

## D-018: Dashboard polling is a presentation boundary, not evidence storage

**Decision:** The frequently polled `/dashboard` endpoint returns compact JSON,
keeps active results and the newest completed result per suite/target, omits raw
tool blocks, and bounds the storage chart series to 90 points. Older Runs remain
listed as lifecycle summaries. The transformation uses copies and cannot mutate
persistence objects. `/runs/{id}` and SQLite remain the authoritative complete
Run evidence, including raw tool output and full-resolution time series.

**Reason:** Re-sending every raw tool block and historical time-series point
every five seconds made the UI pay evidence-export costs for data it does not
render. A documented presentation boundary improves local responsiveness while
preserving reproducibility and auditability at the Run endpoint.

## D-019: Network v5 brackets load with route-derived interface counters

**Decision:** Advance the standard peer profile to `network-v5` and add one
pre-load and one post-load structured interface snapshot on each Agent. The
Agent derives the interface from the exact paired-peer route and reads
cumulative RX/TX bytes, packets, errors, and drops through fixed `ip -s -j link`
arguments. CloudMark computes deltas only when the interface is unchanged, all
counters exist, and none decrease. A complete counter window joins route/NIC
identity and Generator headroom as a comparison-validity requirement. Non-zero
drops or errors remain valid measured evidence and do not hide a poor result.
Legacy network-v2 through network-v4 results retain their original contracts.

**Reason:** iperf3 application metrics cannot show whether the guest interface
dropped packets or recorded errors during the full mixed TCP/UDP test window.
Bracketing the Run adds auditable host-interface evidence without resetting a
counter, changing network configuration, or allowing an arbitrary interface.

## D-020: Network v6 records bounded path observations without inferring ownership

**Decision:** Advance the standard peer profile to `network-v6`. At both Run
boundaries, each Linux Agent executes numeric `tracepath` only toward the exact
paired address with a fixed eight-hop ceiling. CloudMark normalizes at most one
observation per hop, records destination and hop address classes, requires both
bounded traces to reach their paired destination, and requires the route
interface, gateway, and source to remain stable for comparison eligibility.
Trace-sequence changes remain observational because equal-cost multipath can
legitimately vary a hop. Every trace and aggregate path claim explicitly sets
public-Internet traversal to unproven. Network-v2 through network-v5 evidence
retains its original validity contract.

**Reason:** A global-unicast endpoint and a list of visible IP hops are useful
reproducibility facts, but neither establishes administrative ownership or
proves that traffic crossed the public Internet. Bounded destination-reaching
traces and stable route boundaries improve provider comparison evidence while
keeping the claim narrower than the measurement.

## D-021: Repeated network campaigns are immutable and manually dispatched

**Decision:** `network-campaign-v1` binds one pairing session, Target/Generator
identity, topology evidence class, `network-peer-standard` profile version,
and methodology for 3-30 distinct UTC-day windows. Creation is side-effect
free. Every attempt requires explicit network-load and campaign-window
confirmation. Progress is projected from immutable Runs; only completed
comparison-eligible results count, with at most one valid result per UTC day.
Failures may be retried and remain visible. Completion is temporal evidence for
one pair and never enables a provider rating.

**Reason:** Repeated sampling is useful only when the measurement contract stays
stable and an operator controls every load window. Separating acquisition from
provider inference prevents a scheduler, duplicate Run, failed attempt, or
single repeatedly tested pair from overstating evidence quality.
