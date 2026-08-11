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
