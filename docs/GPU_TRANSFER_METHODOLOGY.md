# GPU transfer v1 (CLI implementation)

Profile `gpu-transfer-quick` version `1.0`, methodology `gpu-transfer-v1`,
measures synchronous host-to-device, device-to-host, and same-device buffer
copy. Each phase uses one warm-up and 16 measured copies of 16 MiB. Wall-clock
samples include host API overhead; bytes/s counts payload bytes once per copy.
Results include the selected OpenCL device, runtime versions, allocation plan,
SHA-256 verification, host headroom, and cleanup evidence.

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
