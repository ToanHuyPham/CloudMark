# GPU environment methodology

`gpu-environment-v1` is a bounded read-only inventory contract for accelerators
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

## Runtime discovery

Inventory records only whether fixed runtime or diagnostic interfaces are
present: `nvidia-smi`, `rocm-smi`, `clinfo`, `vulkaninfo`, `vainfo`, and
`ffmpeg`.

Tool presence does not verify CUDA, ROCm, OpenCL, Vulkan, VA-API, codec, or
framework functionality. It also does not prove the device can be opened by a
workload.

## Evidence states

- `complete`: Linux DRM sysfs was readable and every retained device exposed
  the required PCI identity and driver fields. A complete scan with zero
  devices is `not-detected`, not a zero-performance result.
- `partial`: one or more retained devices had incomplete bounded identity or
  more than 16 cards were exposed.
- `unavailable`: Linux DRM sysfs was unavailable or the platform is not yet
  supported by this collector.

Missing or unavailable evidence never becomes a performance value. Windows and
macOS hardware collection remain future work.

## Privacy and claim boundary

CloudMark does not persist GPU UUIDs, serial numbers, IOMMU group identifiers,
raw sysfs paths, management-process lists, or arbitrary command output. PCI
addresses are guest-visible topology, not physical-host placement proof.
SR-IOV/IOMMU visibility does not establish tenant isolation.

The collector does not measure H2D/D2H or peer-to-peer transfer, VRAM
bandwidth, floating-point/tensor compute, training/inference throughput,
framework compatibility, codec throughput, temperature, power, clocks,
throttling, sustained stability, multi-tenant contention, or physical-host
attachment.

The GPU domain is therefore `Partial`. AI/ML, VDI, and media suitability remain
blocked until separately reviewed, fixed, cancellable workload profiles exist.
