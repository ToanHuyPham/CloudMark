# Workload suitability methodology

CloudMark `suitability-v1` classifies one observed target against versioned
workload requirement contracts. It does not average unrelated benchmarks into
one synthetic score, and it does not convert missing evidence into zero.

## Evaluation scope

An evaluation is scoped to one Controller host or one authenticated Agent ID.
Single-system compute, memory, and storage results are associated through their
explicit execution target. PostgreSQL and Web/API/TLS evidence belongs to the
paired Target. Bidirectional network evidence belongs to both participating
Agents. Results from different target IDs are never combined to complete one
target's workload gates.

Every accepted metric retains:

- Run ID;
- benchmark profile and methodology version;
- observation time;
- evidence source and unit;
- Quick, Standard, specialized, or observed-fact quality; and
- freshness status.

Completed runs are accepted only when their methodology is recognized.
Storage results require verified test-file removal. PostgreSQL and Web/API/TLS
results require verified ephemeral-service cleanup. Failed, cancelled,
incompatible, or cleanup-unverified runs cannot satisfy a gate. Evidence older
than 30 days is retained but marked stale and does not satisfy the current
classification.

## Requirement levels

`workload-requirements-1.0` defines three explicit levels:

| Level | Intended interpretation |
|---|---|
| Essential | Entry production or light-duty baseline |
| Standard | General production baseline with stronger capacity and latency gates |
| Demanding | High-demand baseline with tighter latency and higher sustained throughput |

These are CloudMark workload contracts, not claims that every application has
the same requirements. Changing a threshold requires a new requirements
version so an older conclusion remains reproducible.

## Verdicts

| Verdict | Meaning |
|---|---|
| `Insufficient evidence` | One or more required measurements are unavailable/stale, or the required executor is not implemented |
| `Below requirement` | Every required metric is available, but at least one hard gate fails |
| `Conditional fit` | Every measured hard gate passes, but known capability evidence remains outside the current methodology |
| `Suitable` | Every hard gate and required capability is implemented, available, fresh, and passing |

CloudMark reports evidence coverage separately from the pass rate among
measured checks. Neither percentage is a provider quality score. A 100% pass
rate over 20% evidence coverage remains `Insufficient evidence`.

## Current workload contracts

- **Storage & Backup:** sequential read/write and verified cleanup; snapshot,
  object durability, restore, availability, and cost remain limitations.
- **Web & App Hosting:** CPU/RAM, HTTPS API throughput/success/P95, bundled
  dynamic reverse-proxy evidence, Generator CPU validity, HTTP/2 negotiation,
  and directional network throughput/latency; database-backed upstreams,
  HTTP/2 load, HTTP/3, WAF, CDN, autoscaling, and public TLS trust remain
  limitations.
- **Dev & Test:** CPU/RAM, sustained compute, low-queue random read, and write
  throughput; provisioning, images, clone/snapshot, automation, and cost remain
  limitations.
- **Database Management:** CPU/RAM, durable synchronous storage, PostgreSQL
  durable transaction TPS/latency/failures, fixed-count P95/P99, Generator CPU
  validity, same-Target logical backup/restore, and network latency; checkpoint
  isolation, physical/PITR and cross-zone recovery, replication, other engines,
  and managed services remain
  limitations.
- **Networking & Connectivity:** directional TCP floor, idle latency/loss, and
  adaptive UDP loss/jitter; route, MTU, DNS, IPv6, public and cross-location
  paths, repeated windows, and generator saturation remain limitations.
- **Big Data, AI/ML, Containers/Kubernetes, DR, VDI, Media, and Enterprise:**
  available foundation metrics are shown, but their missing domain-specific
  executor is a hard blocker. They remain `Insufficient evidence`.

The API response exposes every exact numeric threshold and comparison operator
for the selected requirement level. The code catalog is the normative source
for `workload-requirements-1.0`.

## Provider evaluation readiness

A target verdict is not a provider verdict. CloudMark keeps provider status
`not-rated` until, at minimum, all of the following are available:

1. verified provider and product/SKU identity;
2. three or more independent targets of the same product;
3. three or more measurement windows;
4. equivalent compute, memory, storage, and network profiles; and
5. security, reliability, control-plane, and timestamped cost evidence.

Future provider-rating aggregation must preserve sample count, median, P10/P90,
worst observed value, zones, time windows, and methodology compatibility. SLA,
durability, availability, compliance, and managed-service claims require their
own documents or controlled drills; VM performance cannot substitute for them.

### Repeated-window descriptive observations

`provider-observations-v4` implements the non-rating portion of that
aggregation. It creates an exact cohort from provider, product/SKU, region, and
operating system, then separates every metric again by profile, methodology,
unit, paired topology, and topology evidence class. Database and cache evidence
is separated again by engine implementation and exact server version. Cross-SKU,
cross-region, cross-OS, cross-methodology, cross-topology,
cross-evidence-class, and cross-database-implementation merging is forbidden. A
paired network Run is one observation even when both endpoints belong to the
same cohort.

Topology evidence is `operator-declared`, `independently-derived`,
`contradicted`, or `unavailable`. Trusted provider metadata can derive
same-zone, cross-zone, or cross-region placement. Globally routable advertised
peer endpoints do not prove public-Internet traversal. A contradiction makes
the metric observational. An operator declaration remains a separate contract for
providers without trusted metadata and is never relabelled as verified.

One UTC calendar day derived from the completed Run timestamp is one
measurement window. A metric cohort is labelled `comparable` only when it has:

- at least nine valid fresh samples;
- at least three participating targets;
- at least three UTC-day windows; and
- independently verified provider identity.

Smaller samples remain `observational`. Both states expose the complete Run ID
set, sample/target/window counts, median, P10, P90, actual minimum and maximum,
direction-aware best and worst values, and P10-P90 relative spread. Relative
spread is labelled stable at no more than 10%, moderate at no more than 25%,
and variable above 25%. Fewer than three samples are labelled
`insufficient-sampling`; a zero median with non-zero spread is variable without
an artificial relative percentage.

These values describe an exact cohort and do not select a winner. Provider
rating remains `not-rated` until the separate security, reliability,
control-plane, cost, and product-claim gates are implemented.

## Interpretation discipline

Thresholds express a minimum requirement, not a universal ranking. Compare
targets only when the workload contract, profile/methodology versions, topology,
Generator class, operating system, and observation window are compatible. Use
the dashboard's blockers and next-evidence guidance before acting on a
conditional conclusion.
