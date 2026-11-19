# GPU transfer v1 (CLI implementation)

Profile `gpu-transfer-quick` version `1.0`, methodology `gpu-transfer-v1`,
measures synchronous host-to-device, device-to-host, and same-device buffer
copy. Each phase uses one warm-up and 16 measured copies of 16 MiB. Wall-clock
samples include host API overhead; bytes/s counts payload bytes once per copy.
Results include the selected OpenCL device, runtime versions, allocation plan,
SHA-256 verification, host headroom, and cleanup evidence.

Each admitted measurement also includes `gpu-transfer-summary-v1`: minimum,
maximum, mean, nearest-rank P50/P95/P99 in milliseconds and population standard
deviation divided by mean (coefficient of variation, percent). The Controller
recomputes these fields from the 16 samples. With only 16 observations P95 and
P99 both select the maximum; these are descriptive sample summaries, not
production tail-latency estimates, stability ratings, or suitability gates.

Both local and remote result admission reconstruct the planning contract from
the selected platform/device evidence. Inconsistent memory budgets, unavailable
devices, invalid platform indexes, altered interpretation, and aggregate timed
samples exceeding 90 seconds are rejected. Reported bandwidth is recomputed
from samples; validation does not establish independent hardware attestation.

Two device buffers are allocated. Host headroom must preserve 512 MiB beyond a
64 MiB allocation budget; Linux checks cgroup headroom. Device capacity must
preserve a 256 MiB capacity margin and each buffer must fit the maximum allocation.
OpenCL does not establish free VRAM, so allocation failure remains possible.
The worker rechecks identity and limits before creating resources.

The isolated child has cooperative checkpoints; the parent terminates a stalled
worker at 90 seconds. Success requires reverse-order resource release and
checksum equality. A killed worker has no verified explicit-cleanup claim.
No kernel is compiled. Unified-memory devices are labelled shared-memory-copy.

Controller/Agent dispatch and dashboard run/cancel actions are implemented with
fixture-only validation. Hardware acceptance and provider aggregation remain
pending. Do not interpret these measurements as PCIe link capacity or GPU
compute capability.

Transfer and synchronization semantics follow the
[OpenCL API specification](https://registry.khronos.org/OpenCL/specs/unified/html/OpenCL_API.html).
