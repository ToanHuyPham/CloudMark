# GPU environment methodology

`gpu-environment-v3` is a bounded read-only inventory contract for accelerators
visible to the CloudMark process. It does not execute a GPU workload and does
not produce a suitability score.

## Linux acquisition

CloudMark inspects at most 16 exact `/sys/class/drm/cardN/device` entries. Each
source file is capped at 4 KiB. Display-class PCI devices may retain:

- DRM card and normalized PCI address;
- PCI vendor, device, subsystem, and display-class identifiers;
- a normalized vendor label and guest driver name;
- guest-visible NUMA node;
- VRAM or local-memory capacity when the driver exposes a bounded byte value;
- SR-IOV physical/virtual-function context and visible VF counts; and
- whether an IOMMU group is visible, without retaining its identifier.

For an NVIDIA device, CloudMark may execute one fixed three-second
`nvidia-smi` inventory query. Output is rejected above 64 KiB and at most 16
rows are accepted. It can add product name, driver version, and visible VRAM.
The command does not request utilization, processes, UUIDs, serials, power,
temperature, or clocks. Unknown fields and raw `uevent` content are discarded.

## Windows acquisition

CloudMark runs one fixed five-second PowerShell query over
`Win32_VideoController`. JSON output is rejected above 64 KiB and more than 16
controllers are refused. The projection contains only name, driver version,
`AdapterRAM`, and PNP device identity. CloudMark extracts PCI vendor, device,
and subsystem IDs, then discards the raw PNP instance identifier.

Windows CIM `AdapterRAM` is an unsigned 32-bit observation with driver-specific
semantics. CloudMark labels it as `windows-cim-adapterram-uint32` and sets
`memory_capacity_may_be_truncated=true`; it must not be interpreted as complete
VRAM capacity for modern devices. PCI address, NUMA, SR-IOV, and IOMMU evidence
remain unavailable on this path.

## Isolated OpenCL backend probe

When the operating-system OpenCL loader is present, CloudMark starts one fixed
five-second `gpu-opencl-probe-v1` child process. Windows loads `OpenCL.dll` from
System32 only; Linux and macOS use fixed loader discovery/fallback names. The
worker enumerates at most 8 platforms and 16 GPU devices and caps each returned
runtime label at 4 KiB.

The normalized evidence can include platform/device/vendor/driver/OpenCL C
versions, global memory, maximum allocation size, address width, device and
compiler availability, and unified-memory status. Raw extension lists are not
persisted.

The worker calls only platform/device enumeration and information functions. It
does not create a context, command queue, buffer, program, or kernel. A crash,
timeout, malformed payload, stderr output, count overflow, or policy mismatch
becomes `unavailable` and does not affect the parent Controller/Agent process.
Successful enumeration verifies a visible OpenCL GPU backend, not that a real
allocation, transfer, compilation, or workload succeeds.

## Runtime discovery

Inventory records only whether fixed runtime or diagnostic interfaces are
present: `nvidia-smi`, `rocm-smi`, `clinfo`, `vulkaninfo`, `vainfo`, and
`ffmpeg`.

Tool presence does not verify CUDA, ROCm, OpenCL, Vulkan, VA-API, codec, or
framework functionality. It also does not prove the device can be opened by a
workload.

## Evidence states

- `complete`: the Linux DRM scan or Windows CIM projection completed and every
  retained device exposed the required platform identity. A complete scan with
  zero devices is `not-detected`, not a zero-performance result.
- `partial`: one or more retained devices had incomplete bounded identity or
  more than 16 cards were exposed.
- `unavailable`: the bounded platform source or fixed query was unavailable,
  malformed, oversized, or unsupported.

Missing or unavailable evidence never becomes a performance value. macOS
hardware collection remains future work.

## Privacy and claim boundary

CloudMark does not persist GPU UUIDs, serial numbers, IOMMU group identifiers,
raw Windows PNP instance IDs, raw sysfs paths, management-process lists, or
arbitrary command output. PCI
addresses are guest-visible topology, not physical-host placement proof.
SR-IOV/IOMMU visibility does not establish tenant isolation.

The collector does not measure H2D/D2H or peer-to-peer transfer, VRAM
bandwidth, floating-point/tensor compute, training/inference throughput,
framework compatibility, codec throughput, temperature, power, clocks,
throttling, sustained stability, multi-tenant contention, or physical-host
attachment.

The GPU domain is therefore `Partial`. AI/ML, VDI, and media suitability remain
blocked until separately reviewed, fixed, cancellable workload profiles exist.
