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
        "latency": None,
        "udp_rate_fractions": [],
        "bidirectional_streams": None,
        "cloud_to_controller": False,
        "profile_version": "1.0",
        "methodology_version": "network-v1",
    },
    "network-peer-standard": {
        "label": "Provider Internal Network",
        "description": "Pre/post peer route, bounded path-trace, aggregate and driver-exposed per-queue counter, NIC, TCP-control, MTU, and system-resolver evidence; TCP scaling; idle latency; loaded TCP RTT; adaptive UDP loss/jitter sweeps; simultaneous bidirectional throughput; and Generator-headroom validation.",
        "requires_agents": 2,
        "directions": ["generator-to-target", "target-to-generator"],
        "tcp_streams": [1, 4, 8, 16],
        "duration_seconds": 15,
        "latency": {"count": 20, "interval_ms": 100, "timeout_ms": 1000},
        "udp_rate_fractions": [0.25, 0.5, 0.9],
        "udp_min_rate_bps": 1_000_000,
        "udp_max_rate_bps": 1_000_000_000,
        "udp_duration_seconds": 15,
        "bidirectional_streams": 4,
        "bidirectional_duration_seconds": 15,
        "path_probe": True,
        "post_path_probe": True,
        "resolver_probe": True,
        "generator_cpu_limit_percent": 90,
        "generator_scaling_cpu_floor_percent": 85,
        "generator_scaling_gain_floor_percent": 5,
        "cloud_to_controller": False,
        "profile_version": "8.0",
        "methodology_version": "network-v8",
    }
}


DATABASE_PROFILES: dict[str, dict[str, Any]] = {
    "postgres-peer-quick": {
        "label": "PostgreSQL Peer Quick",
        "description": "Short two-Agent PostgreSQL baseline with read-only and durable read/write pgbench workloads.",
        "estimated_minutes": 5,
        "requires_agents": 2,
        "engine": "postgresql",
        "scale_factor": 10,
        "port": 55432,
        "profile_version": "1.0",
        "methodology_version": "database-postgresql-v1",
        "jobs": [
            {"name": "select-only-c1", "workload": "select-only", "clients": 1, "threads": 1, "duration": 15, "warmup": 3},
            {"name": "select-only-c4", "workload": "select-only", "clients": 4, "threads": 2, "duration": 20, "warmup": 3},
            {"name": "tpcb-like-c4", "workload": "tpcb-like", "clients": 4, "threads": 2, "duration": 30, "warmup": 3},
        ],
    },
    "postgres-peer-standard": {
        "label": "PostgreSQL Peer Standard",
        "description": "Concurrency scaling, durable transactions, and connection-churn evidence against an isolated PostgreSQL dataset.",
        "estimated_minutes": 12,
        "requires_agents": 2,
        "engine": "postgresql",
        "scale_factor": 50,
        "port": 55432,
        "profile_version": "1.0",
        "methodology_version": "database-postgresql-v1",
        "jobs": [
            {"name": "select-only-c1", "workload": "select-only", "clients": 1, "threads": 1, "duration": 30, "warmup": 5},
            {"name": "select-only-c4", "workload": "select-only", "clients": 4, "threads": 2, "duration": 45, "warmup": 5},
            {"name": "select-only-c16", "workload": "select-only", "clients": 16, "threads": 4, "duration": 60, "warmup": 5},
            {"name": "tpcb-like-c1", "workload": "tpcb-like", "clients": 1, "threads": 1, "duration": 30, "warmup": 5},
            {"name": "tpcb-like-c4", "workload": "tpcb-like", "clients": 4, "threads": 2, "duration": 45, "warmup": 5},
            {"name": "tpcb-like-c16", "workload": "tpcb-like", "clients": 16, "threads": 4, "duration": 60, "warmup": 5},
            {"name": "connection-churn-c4", "workload": "select-only", "clients": 4, "threads": 2, "duration": 30, "warmup": 3, "connect_per_transaction": True},
        ],
    },
}


WEB_PROFILES: dict[str, dict[str, Any]] = {
    "web-peer-quick": {
        "label": "Web & TLS Peer Quick",
        "description": "Short HTTP, HTTPS, keep-alive, TLS connection, JSON, and static-transfer baseline between paired Agents.",
        "estimated_minutes": 4,
        "requires_agents": 2,
        "engine": "nginx",
        "http_port": 58080,
        "https_port": 58443,
        "profile_version": "1.0",
        "methodology_version": "web-http-v1",
        "jobs": [
            {"name": "http-api-c1", "scheme": "http", "path": "/api/v1/record", "concurrency": 1, "duration": 15, "warmup": 2, "keep_alive": True},
            {"name": "http-api-c16", "scheme": "http", "path": "/api/v1/record", "concurrency": 16, "duration": 20, "warmup": 2, "keep_alive": True},
            {"name": "https-api-c16", "scheme": "https", "path": "/api/v1/record", "concurrency": 16, "duration": 20, "warmup": 2, "keep_alive": True},
            {"name": "https-handshake-c4", "scheme": "https", "path": "/health", "concurrency": 4, "duration": 15, "warmup": 2, "keep_alive": False},
            {"name": "http-asset-256k-c4", "scheme": "http", "path": "/assets/256k.bin", "concurrency": 4, "duration": 20, "warmup": 2, "keep_alive": True},
        ],
    },
    "web-peer-standard": {
        "label": "Web & TLS Peer Standard",
        "description": "HTTP and TLS concurrency curves, connection churn, API-sized responses, and 256 KiB static transfer evidence.",
        "estimated_minutes": 8,
        "requires_agents": 2,
        "engine": "nginx",
        "http_port": 58080,
        "https_port": 58443,
        "profile_version": "1.0",
        "methodology_version": "web-http-v1",
        "jobs": [
            {"name": "http-api-c1", "scheme": "http", "path": "/api/v1/record", "concurrency": 1, "duration": 30, "warmup": 3, "keep_alive": True},
            {"name": "http-api-c16", "scheme": "http", "path": "/api/v1/record", "concurrency": 16, "duration": 40, "warmup": 3, "keep_alive": True},
            {"name": "http-api-c64", "scheme": "http", "path": "/api/v1/record", "concurrency": 64, "duration": 45, "warmup": 3, "keep_alive": True},
            {"name": "https-api-c16", "scheme": "https", "path": "/api/v1/record", "concurrency": 16, "duration": 40, "warmup": 3, "keep_alive": True},
            {"name": "https-api-c64", "scheme": "https", "path": "/api/v1/record", "concurrency": 64, "duration": 45, "warmup": 3, "keep_alive": True},
            {"name": "https-handshake-c4", "scheme": "https", "path": "/health", "concurrency": 4, "duration": 30, "warmup": 3, "keep_alive": False},
            {"name": "http-asset-256k-c4", "scheme": "http", "path": "/assets/256k.bin", "concurrency": 4, "duration": 35, "warmup": 3, "keep_alive": True},
            {"name": "http-asset-256k-c16", "scheme": "http", "path": "/assets/256k.bin", "concurrency": 16, "duration": 40, "warmup": 3, "keep_alive": True},
            {"name": "https-asset-256k-c8", "scheme": "https", "path": "/assets/256k.bin", "concurrency": 8, "duration": 40, "warmup": 3, "keep_alive": True},
        ],
    },
}


ASSESSMENT_DOMAINS: list[dict[str, Any]] = [
    {"id": "system-inventory", "label": "System & Hardware Inventory", "status": "available", "summary": "OS, kernel, CPU, RAM, disks, NICs and runtime capabilities"},
    {"id": "provider-identity", "label": "Provider & Instance Identity", "status": "available", "summary": "Trusted metadata, declared manifests, region, zone and confidence"},
    {"id": "virtualization", "label": "Virtualization & Topology", "status": "partial", "summary": "Hypervisor evidence available; placement and deep topology pending"},
    {"id": "compute", "label": "CPU & Compute", "status": "partial", "summary": "Versioned integer single-, multi-core and sustained profiles available; floating point, crypto and compilation pending"},
    {"id": "memory", "label": "Memory & NUMA", "status": "partial", "summary": "Versioned userspace bandwidth profiles available; true latency, STREAM and NUMA penalties pending"},
    {"id": "storage", "label": "Storage, Filesystem & Object", "status": "available", "summary": "Safe block/filesystem profiles available; object and snapshot tests pending"},
    {"id": "network", "label": "Network & Connectivity", "status": "partial", "summary": "Two-Agent TCP/UDP, idle/loaded latency, bounded path, driver queue, and system-resolver evidence, metadata-aware topology checks, Generator validity, and manual repeated windows available; physical-fabric verification and cross-pair automation pending"},
    {"id": "gpu", "label": "GPU & Accelerators", "status": "roadmap", "summary": "GPU inventory, VRAM, transfer, compute and framework profiles"},
    {"id": "web", "label": "Web, API & TLS", "status": "partial", "summary": "Guarded two-Agent Nginx HTTP/TLS concurrency, tail latency, connection churn, and transfer profiles available"},
    {"id": "database", "label": "Database & Cache", "status": "partial", "summary": "Guarded two-Agent PostgreSQL/pgbench profiles available; MySQL/MariaDB, Redis, replication, and recovery pending"},
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
    {"id": "web-app", "label": "Web & App Hosting", "status": "partial", "primary": "web", "coverage": "HTTP/TLS serving evidence; dynamic application runtime pending"},
    {"id": "dev-test", "label": "Dev & Test", "status": "roadmap", "primary": "compute", "coverage": "Compute profile required"},
    {"id": "database", "label": "Database Management", "status": "partial", "primary": "database", "coverage": "PostgreSQL transaction and storage evidence"},
    {"id": "network", "label": "Networking & Connectivity", "status": "partial", "primary": "network", "coverage": "Directional TCP/UDP, latency, loss, jitter, and duplex evidence"},
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
        "database": DATABASE_PROFILES,
        "web": WEB_PROFILES,
        "domains": ASSESSMENT_DOMAINS,
        "scenarios": SCENARIOS,
    }
