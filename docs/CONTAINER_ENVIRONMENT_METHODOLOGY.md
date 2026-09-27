# Container environment methodology

## Scope

`container-environment-v1` is a bounded read-only inventory observation for the
current CloudMark process on Linux. It distinguishes runtime/orchestrator hints,
an overlay-like root filesystem, and absence of recognized evidence without
claiming physical-host placement or container-engine health.

This is not a container benchmark. It does not pull images, contact a runtime
socket, query Kubernetes, create namespaces/cgroups, start a container, or
measure cold start, overlay I/O, CNI, scheduling, density, or autoscaling.

## Fixed sources and bounds

The collector reads only:

- `/proc/1/cgroup`, capped at 4 KiB;
- `/proc/self/cgroup`, capped at 4 KiB;
- `/proc/self/mountinfo`, capped at 64 KiB; and
- presence of the fixed `/.dockerenv` and `/run/.containerenv` markers.

It recognizes bounded hints for Docker, Podman/libpod, containerd, CRI-O, LXC,
systemd-nspawn, and Kubernetes cgroup naming. It retains only normalized hint
names, cgroup v1/v2 classification, the root filesystem type, fixed-marker
booleans, evidence-source status, and collection policy.

Raw cgroup paths, container/pod IDs, mountinfo rows, mount sources, namespace
identifiers, and marker contents are never persisted. Truncated cgroup text is
not used for hint classification. Missing or truncated sources make the
combined evidence `partial`; unsupported platforms return `unavailable`.

## Interpretation

`container_status` has four states:

- `detected`: at least one fixed marker or recognized cgroup runtime/
  orchestrator hint was observed;
- `suspected`: no runtime/orchestrator hint was observed, but the current root
  filesystem is overlay-like;
- `not-detected`: complete bounded sources contained no recognized evidence;
- `unavailable`: the platform is unsupported.

These are guest-process observations. A `not-detected` result does not prove
that CloudMark runs directly on a physical/virtual host because providers and
runtimes may hide markers or cgroup names. Overlay filesystems may be used
outside containers. Runtime hints do not prove daemon reachability, ownership,
security, Kubernetes control-plane state, or physical-host boundaries.

The evidence remains contextual and unscored. Container/Kubernetes suitability
continues to require separately versioned workload executors.
