# CloudMark

![CloudMark social preview](public/og.png)

CloudMark is a local-first qualification platform for cloud instances, VPS,
bare-metal hosts, and self-hosted cloud infrastructure. It records raw evidence,
runs safe benchmark profiles, and maps results to workload suitability instead
of hiding every result behind one opaque score.

This repository currently contains the first working alpha:

- local controller API with SQLite history;
- cross-platform inventory collection;
- trusted cloud metadata detection for AWS, Azure, and Google Cloud;
- bootstrap planning for Ubuntu/Debian, RHEL/CentOS, and SLES;
- filesystem-safe `fio` profiles with latency percentiles;
- multi-node pairing sessions and explicit network direction policy;
- a responsive local dashboard;
- Python and dashboard build tests.

Cloud-to-controller network measurement is intentionally disabled. Multi-node
network profiles are designed for direct traffic between provider agents.

## Quick start for development

Requirements: Python 3.9+, Node.js 22+, and pnpm.

```powershell
python -m cloudmark serve --data-dir .cloudmark
```

In a second terminal:

```powershell
pnpm install
pnpm run dev
```

Open the local URL printed by the dashboard. The controller prints a token;
enter it under **Controller key** before starting write operations.

## Agent commands

```bash
python -m cloudmark inventory
python -m cloudmark doctor --packs storage,network,database,web
sudo python -m cloudmark bootstrap --packs storage,network,database,web --yes
python -m cloudmark run storage --profile disk-quick --yes
```

Storage runs use a temporary file, keep a safety reserve, never target a raw
device, and remove the test file on completion.

## Documentation

- [Hướng dẫn sử dụng tiếng Việt](docs/USER_GUIDE.vi.md)
- [Architecture](docs/ARCHITECTURE.md)
- [API](docs/API.md)
- [Disk methodology](docs/DISK_METHODOLOGY.md)
- [Network methodology](docs/NETWORK_METHODOLOGY.md)
- [Safety model](docs/SAFETY.md)
- [Roadmap kỹ thuật và ma trận số máy](docs/ROADMAP.vi.md)

## Status

Version `0.1.0-alpha` establishes the safe execution, persistence, API, and
dashboard foundation. Real `fio` execution is available after bootstrap.
Multi-node pairing is implemented; automated peer-to-peer `iperf3`, web,
database, and provider-control-plane executors are the next milestones.

## License

Apache-2.0.
