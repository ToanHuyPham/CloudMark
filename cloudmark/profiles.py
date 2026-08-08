from __future__ import annotations

from typing import Any


STORAGE_PROFILES: dict[str, dict[str, Any]] = {
    "disk-quick": {
        "label": "Disk Quick",
        "description": "Kiểm tra an toàn, ngắn, dùng file tạm 512 MiB.",
        "estimated_minutes": 4,
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
        "description": "Đánh giá database, web và throughput với file tạm 4 GiB.",
        "estimated_minutes": 25,
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
}


NETWORK_PROFILES: dict[str, dict[str, Any]] = {
    "network-peer-standard": {
        "label": "Provider Internal Network",
        "description": "Đo trực tiếp agent cloud A ↔ agent cloud B; không đo cloud → controller.",
        "requires_agents": 2,
        "directions": ["agent-a-to-agent-b", "agent-b-to-agent-a", "bidirectional"],
        "tcp_streams": [1, 4, 8, 16],
        "udp": True,
        "cloud_to_controller": False,
    }
}


SCENARIOS: list[dict[str, Any]] = [
    {"id": "storage-backup", "label": "Storage & Backup", "status": "ready", "primary": "storage"},
    {"id": "web-app", "label": "Web & App Hosting", "status": "planned", "primary": "web"},
    {"id": "dev-test", "label": "Dev & Test", "status": "planned", "primary": "compute"},
    {"id": "database", "label": "Database Management", "status": "ready", "primary": "storage"},
    {"id": "network", "label": "Networking & Connectivity", "status": "ready", "primary": "network"},
    {"id": "big-data", "label": "Big Data & Analytics", "status": "planned", "primary": "compute"},
    {"id": "ai-ml", "label": "AI & Machine Learning", "status": "planned", "primary": "gpu"},
    {"id": "containers", "label": "Container & K8s", "status": "planned", "primary": "container"},
    {"id": "dr", "label": "Disaster Recovery", "status": "planned", "primary": "availability"},
    {"id": "vdi", "label": "Virtual Desktop", "status": "planned", "primary": "gpu"},
    {"id": "media", "label": "Media Processing", "status": "planned", "primary": "media"},
    {"id": "enterprise", "label": "Enterprise Applications", "status": "planned", "primary": "reliability"},
]


def all_profiles() -> dict[str, Any]:
    return {"storage": STORAGE_PROFILES, "network": NETWORK_PROFILES, "scenarios": SCENARIOS}
