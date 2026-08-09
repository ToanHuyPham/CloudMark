from __future__ import annotations

from typing import Any


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
    "network-peer-standard": {
        "label": "Provider Internal Network",
        "description": "Measures cloud agent A ↔ cloud agent B directly; never cloud → controller.",
        "requires_agents": 2,
        "directions": ["agent-a-to-agent-b", "agent-b-to-agent-a", "bidirectional"],
        "tcp_streams": [1, 4, 8, 16],
        "udp": True,
        "cloud_to_controller": False,
    }
}


ASSESSMENT_DOMAINS: list[dict[str, Any]] = [
    {"id": "system-inventory", "label": "System & Hardware Inventory", "status": "available", "summary": "OS, kernel, CPU, RAM, disks, NICs and runtime capabilities"},
    {"id": "provider-identity", "label": "Provider & Instance Identity", "status": "available", "summary": "Trusted metadata, declared manifests, region, zone and confidence"},
    {"id": "virtualization", "label": "Virtualization & Topology", "status": "partial", "summary": "Hypervisor evidence available; placement and deep topology pending"},
    {"id": "compute", "label": "CPU & Compute", "status": "partial", "summary": "CPU topology available; single-thread, multi-thread and sustained tests pending"},
    {"id": "memory", "label": "Memory & NUMA", "status": "partial", "summary": "Capacity available; bandwidth, latency and NUMA penalties pending"},
    {"id": "storage", "label": "Storage, Filesystem & Object", "status": "available", "summary": "Safe block/filesystem profiles available; object and snapshot tests pending"},
    {"id": "network", "label": "Network & Connectivity", "status": "partial", "summary": "Topology and pairing available; guarded traffic executor pending"},
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
    {"id": "network", "label": "Networking & Connectivity", "status": "partial", "primary": "network", "coverage": "Topology and pairing"},
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
        "storage": STORAGE_PROFILES,
        "network": NETWORK_PROFILES,
        "domains": ASSESSMENT_DOMAINS,
        "scenarios": SCENARIOS,
    }
