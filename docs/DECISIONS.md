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
