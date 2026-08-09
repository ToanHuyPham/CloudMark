from __future__ import annotations

from typing import Any


COMPUTE_PROFILES: dict[str, dict[str, Any]] = {
    "compute-quick": {
        "label": "Compute Quick",
        "description": "Short integer CPU scaling and sustained-load checks for baseline qualification.",
        "estimated_minutes": 3,
        "profile_version": "1.0",
        "methodology_version": "compute-v1",
        "jobs": [
            {"name": "integer-single", "threads": 1, "runtime": 15, "warmup": 3, "cpu_max_prime": 20000},
            {"name": "integer-all-cores", "threads": "all", "runtime": 20, "warmup": 3, "cpu_max_prime": 20000},
            {"name": "integer-sustained", "threads": "all", "runtime": 60, "warmup": 5, "cpu_max_prime": 20000},
        ],
    },
    "compute-standard": {
        "label": "Compute Standard",
        "description": "Longer single-, half-, and all-core integer scaling with a five-minute sustained phase.",
        "estimated_minutes": 9,
        "profile_version": "1.0",
        "methodology_version": "compute-v1",
        "jobs": [
            {"name": "integer-single", "threads": 1, "runtime": 30, "warmup": 5, "cpu_max_prime": 20000},
            {"name": "integer-half-cores", "threads": "half", "runtime": 45, "warmup": 5, "cpu_max_prime": 20000},
            {"name": "integer-all-cores", "threads": "all", "runtime": 60, "warmup": 10, "cpu_max_prime": 20000},
            {"name": "integer-sustained", "threads": "all", "runtime": 300, "warmup": 15, "cpu_max_prime": 20000},
        ],
    },
}


MEMORY_PROFILES: dict[str, dict[str, Any]] = {
    "memory-quick": {
        "label": "Memory Quick",
        "description": "Short cache-resistant read/copy/triad bandwidth checks at one thread and all logical cores.",
        "estimated_minutes": 3,
        "profile_version": "1.0",
        "methodology_version": "memory-v1",
        "array_size_mib": 128,
        "jobs": [
            {"name": "read-single", "kernel": "read", "threads": 1, "runtime": 15},
            {"name": "copy-single", "kernel": "copy", "threads": 1, "runtime": 15},
            {"name": "read-all-cores", "kernel": "read", "threads": "all", "runtime": 20},
            {"name": "copy-all-cores", "kernel": "copy", "threads": "all", "runtime": 20},
            {"name": "triad-all-cores", "kernel": "triad", "threads": "all", "runtime": 20},
        ],
    },
    "memory-standard": {
        "label": "Memory Standard",
        "description": "Longer read, write, copy and triad bandwidth checks with a 768 MiB total working set.",
        "estimated_minutes": 8,
        "profile_version": "1.0",
        "methodology_version": "memory-v1",
        "array_size_mib": 256,
        "jobs": [
            {"name": "read-single", "kernel": "read", "threads": 1, "runtime": 30},
            {"name": "write-single", "kernel": "write", "threads": 1, "runtime": 30},
            {"name": "copy-single", "kernel": "copy", "threads": 1, "runtime": 30},
            {"name": "triad-single", "kernel": "triad", "threads": 1, "runtime": 30},
            {"name": "read-all-cores", "kernel": "read", "threads": "all", "runtime": 45},
            {"name": "write-all-cores", "kernel": "write", "threads": "all", "runtime": 45},
            {"name": "copy-all-cores", "kernel": "copy", "threads": "all", "runtime": 45},
            {"name": "triad-all-cores", "kernel": "triad", "threads": "all", "runtime": 45},
        ],
    },
}


STORAGE_PROFILES: dict[str, dict[str, Any]] = {
    "disk-quick": {
        "label": "Disk Quick",
        "description": "A short, safe assessment using a 512 MiB temporary file.",
        "estimated_minutes": 4,
        "profile_version": "1.1",
        "methodology_version": "storage-v1",
        "file_size_mib": 512,
        "jobs": [
            {"name": "sequential-read", "rw": "read", "bs": "1m", "iodepth": 8, "runtime": 15},
            {"name": "sequential-write", "rw": "write", "bs": "1m", "iodepth": 8, "runtime": 15},
            {"name": "random-read-qd1", "rw": "randread", "bs": "4k", "iodepth": 1, "runtime": 20},
            {"name": "mixed-70-30", "rw": "randrw", "rwmixread": 70, "bs": "4k", "iodepth": 16, "runtime": 30},
        ],
    },
    "disk-standard": {
        "label": "Disk Standard",
        "description": "Database, web, and throughput assessment using a 4 GiB temporary file.",
        "estimated_minutes": 25,
        "profile_version": "1.1",
        "methodology_version": "storage-v1",
        "file_size_mib": 4096,
        "jobs": [
            {"name": "sequential-read", "rw": "read", "bs": "1m", "iodepth": 8, "runtime": 60},
            {"name": "sequential-write", "rw": "write", "bs": "1m", "iodepth": 8, "runtime": 60},
            {"name": "random-read-qd1", "rw": "randread", "bs": "4k", "iodepth": 1, "runtime": 60},
            {"name": "random-read-qd32", "rw": "randread", "bs": "4k", "iodepth": 32, "runtime": 90},
            {"name": "random-write-qd1", "rw": "randwrite", "bs": "4k", "iodepth": 1, "runtime": 60},
            {"name": "mixed-70-30", "rw": "randrw", "rwmixread": 70, "bs": "4k", "iodepth": 32, "runtime": 300},
            {"name": "database-sync", "rw": "randwrite", "bs": "8k", "iodepth": 1, "runtime": 120, "fsync": 1},
        ],
    },
    "disk-database": {
        "label": "Disk Database",
        "description": "Latency and durability-sensitive 8 KiB database access patterns using a 2 GiB temporary file.",
        "estimated_minutes": 14,
        "profile_version": "1.0",
        "methodology_version": "storage-v1",
        "file_size_mib": 2048,
        "jobs": [
            {"name": "database-read-qd1", "rw": "randread", "bs": "8k", "iodepth": 1, "runtime": 60},
            {"name": "database-write-qd1", "rw": "randwrite", "bs": "8k", "iodepth": 1, "runtime": 60},
            {"name": "database-read-qd16", "rw": "randread", "bs": "8k", "iodepth": 16, "runtime": 90},
            {"name": "database-mixed-70-30", "rw": "randrw", "rwmixread": 70, "bs": "8k", "iodepth": 8, "runtime": 180},
            {"name": "database-sync", "rw": "randwrite", "bs": "8k", "iodepth": 1, "runtime": 120, "fsync": 1},
        ],
    },
    "disk-throughput": {
        "label": "Disk Throughput",
        "description": "Large-block read/write scaling for backup, media, and analytics using a 4 GiB temporary file.",
        "estimated_minutes": 12,
        "profile_version": "1.0",
        "methodology_version": "storage-v1",
        "file_size_mib": 4096,
        "jobs": [
            {"name": "sequential-read-qd1", "rw": "read", "bs": "1m", "iodepth": 1, "runtime": 60},
            {"name": "sequential-read-qd16", "rw": "read", "bs": "1m", "iodepth": 16, "runtime": 90},
            {"name": "sequential-write-qd1", "rw": "write", "bs": "1m", "iodepth": 1, "runtime": 60},
            {"name": "sequential-write-qd16", "rw": "write", "bs": "1m", "iodepth": 16, "runtime": 90},
            {"name": "streaming-mixed", "rw": "rw", "rwmixread": 70, "bs": "128k", "iodepth": 8, "runtime": 180},
        ],
    },
    "disk-sustained": {
        "label": "Disk Sustained",
        "description": "Long steady-state mixed I/O for burst-credit and throttling detection using an 8 GiB temporary file.",
        "estimated_minutes": 25,
        "profile_version": "1.0",
        "methodology_version": "storage-v1",
        "file_size_mib": 8192,
        "jobs": [
            {"name": "sustained-random-read", "rw": "randread", "bs": "4k", "iodepth": 32, "runtime": 300, "ramp_time": 15},
            {"name": "sustained-mixed-70-30", "rw": "randrw", "rwmixread": 70, "bs": "4k", "iodepth": 32, "runtime": 600, "ramp_time": 20},
            {"name": "sustained-sequential-write", "rw": "write", "bs": "1m", "iodepth": 8, "runtime": 300, "ramp_time": 10},
        ],
    },
}


NETWORK_PROFILES: dict[str, dict[str, Any]] = {
    "network-peer-quick": {
        "label": "Provider Peer Quick",
        "description": "Short TCP measurements in both directions between paired provider agents.",
        "requires_agents": 2,
        "directions": ["generator-to-target", "target-to-generator"],
        "tcp_streams": [1, 4],
        "duration_seconds": 10,
        "udp": False,
        "cloud_to_controller": False,
        "profile_version": "1.0",
        "methodology_version": "network-v1",
    },
    "network-peer-standard": {
        "label": "Provider Internal Network",
        "description": "Measures cloud agent A to B and B to A directly; never cloud to controller.",
        "requires_agents": 2,
        "directions": ["generator-to-target", "target-to-generator"],
        "tcp_streams": [1, 4, 8, 16],
        "duration_seconds": 15,
        "udp": False,
        "cloud_to_controller": False,
        "profile_version": "1.0",
        "methodology_version": "network-v1",
    }
}


ASSESSMENT_DOMAINS: list[dict[str, Any]] = [
    {"id": "system-inventory", "label": "System & Hardware Inventory", "status": "available", "summary": "OS, kernel, CPU, RAM, disks, NICs and runtime capabilities"},
    {"id": "provider-identity", "label": "Provider & Instance Identity", "status": "available", "summary": "Trusted metadata, declared manifests, region, zone and confidence"},
    {"id": "virtualization", "label": "Virtualization & Topology", "status": "partial", "summary": "Hypervisor evidence available; placement and deep topology pending"},
    {"id": "compute", "label": "CPU & Compute", "status": "partial", "summary": "Versioned integer single-, multi-core and sustained profiles available; floating point, crypto and compilation pending"},
    {"id": "memory", "label": "Memory & NUMA", "status": "partial", "summary": "Versioned userspace bandwidth profiles available; true latency, STREAM and NUMA penalties pending"},
    {"id": "storage", "label": "Storage, Filesystem & Object", "status": "available", "summary": "Safe block/filesystem profiles available; object and snapshot tests pending"},
    {"id": "network", "label": "Network & Connectivity", "status": "partial", "summary": "Guarded two-agent TCP executor available; UDP and loaded-latency profiles pending"},
    {"id": "gpu", "label": "GPU & Accelerators", "status": "roadmap", "summary": "GPU inventory, VRAM, transfer, compute and framework profiles"},
    {"id": "web", "label": "Web, API & TLS", "status": "roadmap", "summary": "HTTP, TLS, concurrency, tail latency and saturation profiles"},
    {"id": "database", "label": "Database & Cache", "status": "roadmap", "summary": "PostgreSQL, MySQL/MariaDB, Redis and persistence profiles"},
    {"id": "containers", "label": "Containers & Kubernetes", "status": "partial", "summary": "Runtime discovery available; image, pod, network and scaling tests pending"},
    {"id": "security", "label": "Security & Isolation", "status": "roadmap", "summary": "IAM, firewall, exposure, tenant isolation and hardening evidence"},
    {"id": "reliability", "label": "Reliability, HA & DR", "status": "roadmap", "summary": "Failover, replication, snapshot, restore, RPO and RTO drills"},
    {"id": "observability", "label": "Observability & Operations", "status": "roadmap", "summary": "Metrics, logs, traces, clock sync, alerting and operational overhead"},
    {"id": "control-plane", "label": "Provisioning & Control Plane", "status": "roadmap", "summary": "Create, resize, attach, snapshot and API reliability measurements"},
    {"id": "cost", "label": "Cost & Efficiency", "status": "roadmap", "summary": "Timestamped pricing, price/performance and resource efficiency"},
    {"id": "consistency", "label": "Consistency & Noisy Neighbor", "status": "roadmap", "summary": "Cross-instance variance, throttling, steal time and time-window stability"},
]


SCENARIOS: list[dict[str, Any]] = [
    {"id": "storage-backup", "label": "Storage & Backup", "status": "available", "primary": "storage", "coverage": "Block storage performance"},
    {"id": "web-app", "label": "Web & App Hosting", "status": "roadmap", "primary": "web", "coverage": "Application executor required"},
    {"id": "dev-test", "label": "Dev & Test", "status": "roadmap", "primary": "compute", "coverage": "Compute profile required"},
    {"id": "database", "label": "Database Management", "status": "partial", "primary": "storage", "coverage": "Storage evidence only"},
    {"id": "network", "label": "Networking & Connectivity", "status": "partial", "primary": "network", "coverage": "Two-direction TCP peer measurements"},
    {"id": "big-data", "label": "Big Data & Analytics", "status": "roadmap", "primary": "compute", "coverage": "Distributed profile required"},
    {"id": "ai-ml", "label": "AI & Machine Learning", "status": "roadmap", "primary": "gpu", "coverage": "Accelerator profile required"},
    {"id": "containers", "label": "Container & K8s", "status": "roadmap", "primary": "container", "coverage": "Orchestration profile required"},
    {"id": "dr", "label": "Disaster Recovery", "status": "roadmap", "primary": "availability", "coverage": "Recovery drill required"},
    {"id": "vdi", "label": "Virtual Desktop", "status": "roadmap", "primary": "gpu", "coverage": "Interactive graphics profile required"},
    {"id": "media", "label": "Media Processing", "status": "roadmap", "primary": "media", "coverage": "Codec profile required"},
    {"id": "enterprise", "label": "Enterprise Applications", "status": "roadmap", "primary": "reliability", "coverage": "Operational evidence required"},
]


def all_profiles() -> dict[str, Any]:
    return {
        "compute": COMPUTE_PROFILES,
        "memory": MEMORY_PROFILES,
        "storage": STORAGE_PROFILES,
        "network": NETWORK_PROFILES,
        "domains": ASSESSMENT_DOMAINS,
        "scenarios": SCENARIOS,
    }
