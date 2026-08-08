# Danh mục đánh giá toàn diện CloudMark

Tài liệu này là phạm vi kỹ thuật chuẩn cho CloudMark khi đánh giá cloud, VPS,
bare-metal và hạ tầng public cloud tự xây. Catalog không đồng nghĩa mọi executor
đã sẵn sàng. Dashboard và API luôn công bố một trong ba trạng thái:

- `Available`: đã có bằng chứng hoặc executor có thể dùng trong bản hiện tại;
- `Partial`: đã có một phần dữ liệu nhưng chưa đủ để kết luận trọn miền;
- `Roadmap`: thuộc phạm vi sản phẩm nhưng chưa được dùng để chấm điểm.

Một nhãn workload chỉ được tạo khi các hard gate của workload đó có đủ metric,
topology hợp lệ, tool health và số mẫu tối thiểu. Thiếu bằng chứng trả về
`Insufficient evidence`, không phải điểm 0.

## Ma trận miền kỹ thuật

| # | Miền | Bằng chứng và phép đo mục tiêu | Topology tối thiểu | Hiện tại |
|---:|---|---|---|---|
| 1 | System & Hardware Inventory | OS/kernel, firmware, CPU topology, RAM, NUMA, disk, filesystem, NIC, clock, runtime/tool capability | 1 máy | Available |
| 2 | Provider & Instance Identity | metadata tin cậy, manifest khai báo, provider/region/zone/SKU, nguồn và confidence | 1 máy | Available |
| 3 | Virtualization & Topology | hypervisor/container evidence, vCPU placement, NUMA exposure, nested virtualization, overcommit indicators | 1 máy; 2–3 SKU để so sánh | Partial |
| 4 | CPU & Compute | single/multi-thread, integer, FP, compression, crypto, compile, sustained throughput, steal/throttle, perf-per-watt khi có | 1 máy; 2–3 instance để đo variance | Partial |
| 5 | Memory & NUMA | bandwidth read/write/copy, latency, remote NUMA penalty, page size, swap pressure, sustained stability | 1 máy | Partial |
| 6 | Storage, Filesystem & Object | sequential/random/mixed I/O, QD sweep, sync/fsync, P50–P99.9, burst/throttle, metadata, integrity; object PUT/GET/list và snapshot/restore | 1 máy cho block; 2–3 cho object/backup | Available |
| 7 | Network & Connectivity | TCP/UDP, 1–16 streams, RTT loaded/idle, jitter/loss/reorder, retransmit, MTU, DNS, IPv4/IPv6, private/public, cross-zone/region | 2 agent + Controller | Partial |
| 8 | GPU & Accelerators | model/driver, VRAM, H2D/D2H, compute, tensor/FP profiles, framework probe, thermal/power stability, media encode/decode | 1 máy GPU; 2 để đo serving | Roadmap |
| 9 | Web, API & TLS | static/JSON, TLS handshake, keep-alive, HTTP/2/3, concurrency ramp, P50–P99, error rate, saturation, soak, reverse proxy | target + generator + Controller | Roadmap |
| 10 | Database & Cache | PostgreSQL/MySQL OLTP, read-only/read-write, connection scaling, checkpoint/fsync, Redis GET/SET/pipeline/persistence, replication lag | server + client; 3+ cho replication | Roadmap |
| 11 | Containers & Kubernetes | runtime discovery, pull/unpack, cold start, overlay I/O, pod density, service latency, CNI, scheduling và autoscaling response | 1 cho container; 2–3+ cho K8s | Partial |
| 12 | Security & Isolation | port/exposure inventory, firewall/security group evidence, TLS posture, IAM/RBAC, hardening, tenant isolation signals và auditability | 1–2 máy; control-plane adapter khi cần | Roadmap |
| 13 | Reliability, HA & DR | replication, controlled failover, load-balancer health, node replacement, snapshot/restore, backup integrity, RPO/RTO drill | 3 agent + Controller; 4 khuyến nghị | Roadmap |
| 14 | Observability & Operations | metrics/logs/traces, clock sync, alert path, agent overhead, log delivery loss, retention/export evidence | 1 máy; 2+ cho delivery path | Roadmap |
| 15 | Provisioning & Control Plane | create/delete/resize, attach/detach, snapshot, API latency/error/rate-limit, quota và idempotency | Controller + adapter quyền tối thiểu | Roadmap |
| 16 | Cost & Efficiency | giá có timestamp/currency/source, egress và storage cost, price/performance, utilization, right-sizing và license context | dữ liệu benchmark + nguồn giá | Roadmap |
| 17 | Consistency & Noisy Neighbor | variance giữa instance/time window, P10/P50/P90, worst observed, burst credit, steal time, throttling và recovery | 2–3 instance cùng SKU, nhiều khung giờ | Roadmap |

`Available` ở cấp miền nghĩa là đã có ít nhất một đường đo hợp lệ, không có nghĩa
mọi phép đo trong dòng đã hoàn tất. Raw result phải ghi phiên bản profile, tool,
methodology, timestamp và topology để phạm vi thực tế luôn kiểm chứng được.

## Ánh xạ sang mục đích sử dụng

Mỗi workload dùng nhiều miền thay vì lấy một benchmark duy nhất:

| Nhu cầu | Hard gate tiêu biểu | Bằng chứng bổ sung |
|---|---|---|
| Storage & Backup | storage integrity, throughput, restore path | network, cost, reliability |
| Web & App Hosting | CPU, memory, network, web/API tail latency | TLS, autoscaling, observability, cost |
| Dev & Test | compute, memory, storage, provisioning | container, snapshot, cost |
| Database Management | fsync/tail latency, CPU, RAM, DB workload | replication, backup/restore, HA, security |
| Networking & Connectivity | RTT/loss/jitter/throughput, DNS | IPv6, firewall, cross-zone, cost |
| Big Data & Analytics | sustained compute, memory, storage throughput | network scale, object storage, cost |
| AI & Machine Learning | GPU/accelerator, VRAM, CPU/RAM, storage | network, framework, cost, consistency |
| Container & K8s | container/K8s, CPU/RAM, network | storage, autoscaling, observability, security |
| Disaster Recovery | backup integrity, replication, failover, RPO/RTO | cross-region network, control plane, operations |
| Virtual Desktop | GPU/media, interactive latency, CPU/RAM | security, connectivity, consistency |
| Media Processing | codec throughput, CPU/GPU, storage throughput | object storage, network/CDN, cost |
| Enterprise Applications | reliability, security, database, operations | IAM/RBAC, control plane, consistency, cost |

## Quy tắc topology

- Bài một máy chỉ kết luận về máy/instance đã đo.
- Bài client–server tách target và generator để tránh tự cạnh tranh tài nguyên.
- Controller chỉ điều phối; không nằm trên data path benchmark provider.
- HA/DR cần node độc lập. VM lồng trên cùng một host chỉ chứng minh chức năng,
  không chứng minh availability của provider fabric.
- Đánh giá nhà cung cấp cần nhiều instance cùng SKU, nhiều zone và nhiều khung
  giờ; báo cáo phải hiện sample count, median, P10/P90 và worst observed.

## Nguyên tắc an toàn

- Không phát traffic tới hệ thống bên thứ ba; web resilience test chỉ chạy trên
  tài nguyên người vận hành sở hữu hoặc được ủy quyền rõ ràng.
- Storage mặc định chỉ dùng file tạm trên filesystem, giữ safety reserve, không
  format, không raw device và luôn cleanup.
- Test gây tải phải có rate/duration limit, watchdog, health check và phương án
  dừng khẩn cấp.
- Provider API adapter dùng quyền tối thiểu và mọi thay đổi tài nguyên phải được
  ghi audit log.
