# Storage campaign methodology

`storage-campaign-v1` acquires time-separated evidence for one fixed storage
target and one exact storage contract. It is a manual acquisition workflow, not
a scheduler and not a provider rating.

## Baseline requirement

A campaign is created from one completed storage Run. Creation is rejected
unless the baseline has:

- an installed storage profile with exact profile and methodology versions;
- every expected fio job or native filesystem operation in exact order, with
  the complete workload, latency, throughput/operation, time-series or
  integrity/durability payload required by its executor;
- verified test-file and executor-artifact cleanup;
- complete `storage-environment-v1` evidence;
- an exact filesystem, bounded mount, applicable guest block, and executor/tool
  contract; and
- matching run-time Target inventory/provider evidence; and
- timezone-aware start/completion timestamps on the same UTC day, with
  completion not preceding start.

The immutable baseline counts as the first valid UTC-day window. Creating the
campaign starts no benchmark and writes no test data.

## Immutable contract

The campaign records:

- baseline Run ID and UTC completion day;
- Controller or Agent target ID and normalized hostname;
- provider identity source, provider/product, region, and zone;
- operating system, release, and architecture;
- storage profile, profile version, and methodology version;
- exact `provider-observations-v6` storage environment/tool contract; and
- a target of 3–30 distinct UTC completion days.

An active campaign for the same target, profile, and storage contract cannot be
created twice. Completed, invalid, or superseded campaigns remain available as
evidence and may be followed by a new campaign.

## Manual window dispatch

Every subsequent Run requires both:

```json
{
  "confirm_write": true,
  "confirm_campaign_window": true
}
```

No background scheduler dispatches storage load. Existing single-target
admission rules still apply: the target must be online and capable, and another
compute, memory, storage, or security Run cannot already occupy it.
The Controller retains both confirmations in the immutable Run request. The
generic Run endpoint rejects campaign metadata, so a caller cannot bypass this
guarded dispatch path.

## Window validation

A Run counts only when:

- its campaign, target, profile, profile version, and methodology match;
- its full executor-specific measurement payload and cleanup contract are
  complete;
- its persisted run-time Target identity matches the baseline;
- its filesystem/mount/block/tool contract exactly matches the baseline;
- it completes successfully; and
- its declared campaign day equals its timezone-aware UTC completion day, and
  start and completion occur on that same UTC day.

At most one valid Run counts per UTC day. Additional valid Runs on the same day
remain normal Run evidence but are labelled duplicate campaign windows. Failed,
cancelled, incomplete, drifted, and cross-midnight attempts remain visible and
do not consume a window.

An unfinished campaign becomes `superseded` when the installed profile or
methodology changes. A missing or altered baseline makes it `invalid`. Target
identity drift or an offline Agent blocks the next dispatch without deleting
previous evidence.

## Interpretation boundary

Completing a campaign establishes temporal evidence for one target. It can show
variation, throttling, or burst-credit behavior across the selected profile and
days. It does not establish provider-wide consistency because independent
same-contract targets are still required by the provider observation model.

Storage performance does not prove snapshots, replication, restore quality,
power-loss protection, availability, SLA, or cost. Those require separate
evidence contracts.
