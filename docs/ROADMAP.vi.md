# Roadmap kỹ thuật CloudMark

Roadmap này tách **đo lường**, **bằng chứng** và **chấm điểm**. CloudMark chỉ kết
luận một máy phù hợp với workload nào khi đã có đủ metric bắt buộc và độ tin cậy
của lần chạy. Không dùng tên CPU, dung lượng RAM hoặc quảng cáo của provider để
thay thế benchmark.

## Phạm vi sản phẩm

Thứ tự milestone bên dưới là **thứ tự triển khai**, không phải giới hạn phạm vi
của CloudMark. Catalog sản phẩm gồm 17 miền kỹ thuật:

1. system/hardware inventory;
2. provider và instance identity;
3. virtualization và topology;
4. CPU/compute;
5. memory/NUMA;
6. storage/filesystem/object;
7. network/connectivity;
8. GPU/accelerator;
9. web/API/TLS;
10. database/cache;
11. container/Kubernetes;
12. security/isolation;
13. reliability/HA/DR;
14. observability/operations;
15. provisioning/control plane;
16. cost/efficiency;
17. consistency/noisy neighbor.

Chi tiết metric, topology tối thiểu và trạng thái từng miền nằm trong
[`ASSESSMENT_CATALOG.vi.md`](ASSESSMENT_CATALOG.vi.md). Tất cả miền cùng cấp về
phạm vi sản phẩm; storage được triển khai sớm vì đây là tiêu chí ưu tiên của
người vận hành và executor an toàn đã trưởng thành hơn.

## Nguyên tắc cố định

- Controller chỉ điều phối và lưu kết quả; không nằm trên data path benchmark.
- Network provider được đo trực tiếp Agent A ↔ Agent B.
- Raw result là dữ liệu gốc, bất biến và có phiên bản methodology/tool/profile.
- Mỗi kết luận phải chỉ ra metric đạt, metric thiếu và điều kiện làm giảm confidence.
- Một lần chạy trên một VM không đại diện cho toàn bộ provider.
- Không suy diễn durability, SLA, snapshot, managed service hoặc compliance từ
  benchmark hiệu năng của một VM.

## M0 — Nền tảng an toàn (đã có trong 0.1.0)

- inventory đa nền tảng;
- nhận diện AWS/Azure/GCP bằng metadata tin cậy;
- manifest có nhãn `declared, unverified` cho cloud Việt Nam/cloud tự xây;
- Controller API, token ghi, SQLite WAL và lịch sử run;
- bootstrap plan cho apt, dnf/yum và zypper;
- `fio` filesystem-safe, file tạm, safety reserve, không raw device;
- pairing session 30 phút, tối đa 8 agent, đủ 2 agent mới `ready`;
- dashboard local và OpenAPI v1.

## M1 — Storage qualification (executor ưu tiên đầu tiên)

### Block/local storage

- sequential 1 MiB read/write, single và multi-job;
- random 4/8/16 KiB tại QD1, QD4, QD16, QD32, QD64;
- mixed 70/30 và 50/50;
- synchronous database write, fsync/fdatasync latency;
- P50/P90/P95/P99/P99.9, bandwidth, IOPS và CPU/IOPS;
- per-second time series để phát hiện burst credit/throttle;
- warm-up, steady-state và cooldown tách riêng;
- nhiều kích thước working set để giảm ảnh hưởng cache;
- filesystem metadata/small-file profile;
- integrity checksum sau write/read;
- SMART/NVMe health chỉ thu thập khi OS/provider cho phép.

### Storage service/backup

- object storage PUT/GET/list/delete với nhiều object size;
- multipart upload, time-to-first-byte và concurrency scaling;
- snapshot create/restore time qua adapter provider;
- backup/restore throughput và kiểm tra checksum;
- RPO/RTO drill cần từ 2–3 node và adapter control-plane.

M1 sẽ xuất các capability riêng: transactional DB, latency-sensitive web,
general purpose, analytics throughput, media scratch, backup target. Không gộp
tất cả vào một “disk score”.

## M2 — Provider-internal network executor

Topology bắt buộc: Controller + Agent A + Agent B.

- TCP A→B, B→A và bidirectional với 1/4/8/16 streams;
- UDP rate sweep, loss, jitter, reorder và practical ceiling;
- idle RTT, loaded RTT và bufferbloat;
- retransmission, congestion control, MTU, route và NIC offload evidence;
- sender/receiver CPU để phát hiện generator bottleneck;
- same-zone, cross-zone, cross-region được gắn nhãn riêng;
- short burst và sustained run;
- mTLS, allow-list port, rate limit, watchdog và cleanup bắt buộc.

Không có profile cloud→controller. Kết quả qua public Internet và private/VPC
network cũng không được trộn chung.

## M3 — Compute, memory và GPU

- CPU single-thread/multi-thread, integer, floating point, compression, crypto;
- sustained CPU để nhận diện steal time, throttling và noisy neighbor;
- memory bandwidth/latency, NUMA topology và remote-node penalty;
- GPU inventory, H2D/D2H bandwidth, compute, VRAM và thermal/power stability;
- media encode/decode thực tế bằng FFmpeg;
- framework probes cho CUDA/ROCm/oneAPI khi có;
- variance giữa nhiều lần chạy và nhiều instance cùng SKU.

## M4 — Database và web/application

Các workload client-server dùng tối thiểu 2 agent để không tự cạnh tranh CPU và
network trên cùng máy.

- PostgreSQL: pgbench read-only, read/write, connection scaling, checkpoint;
- MySQL/MariaDB: OLTP read/write và fsync-sensitive profile;
- Redis: GET/SET, pipeline, persistence, tail latency;
- web tĩnh, API JSON, TLS, keep-alive và concurrency ramp;
- reverse proxy, compression và HTTP/2/HTTP/3 khi được hỗ trợ;
- soak test, error rate, P95/P99 và saturation point;
- DDoS chỉ là **authorized resilience test** trên hệ thống do người dùng sở hữu,
  có giới hạn rate/duration; không phát sinh traffic tới hệ thống bên thứ ba.

## M5 — Container, K8s, HA và vận hành

- container cold start, image pull/unpack và overlay filesystem;
- K8s scheduling, pod density, service latency và autoscaling response;
- load balancer health/failover;
- database replication lag và controlled failover;
- snapshot/restore, node replacement và recovery drill;
- DNS, IPv6, firewall/security group và private connectivity evidence;
- monitoring/logging coverage và clock synchronization;
- provider API create/delete/resize/snapshot latency qua adapter có quyền tối thiểu.

Các bài HA/failover cần 3 node trở lên để tách target, load generator và
replica/witness. VM lồng trong một máy vật lý chỉ phù hợp để kiểm tra chức năng,
không đủ bằng chứng cho availability hoặc provider fabric trong môi trường thật.

## M6 — Suitability và provider score

Mỗi scenario trong bảng nhu cầu có:

1. hard gates — thiếu là không đủ điều kiện;
2. weighted metrics — hiệu năng và tail latency;
3. stability — độ lệch giữa lần chạy/instance/time window;
4. evidence confidence — topology, sample count, tool health;
5. operational evidence — snapshot, failover, API, security;
6. cost input — tách riêng và luôn ghi timestamp/currency/source.

Kết quả đề xuất gồm `Excellent`, `Suitable`, `Conditional`, `Not recommended`
và `Insufficient evidence`. Mỗi nhãn phải có reason codes; không hiển thị một
điểm tổng duy nhất mà không kèm raw metric.

Provider score được tổng hợp từ nhiều máy, nhiều khung giờ và nhiều zone. Báo
cáo phải hiển thị median, P10/P90, worst observed result, sample count và phiên
bản profile. SLA, durability và compliance chỉ được chấm khi có tài liệu hoặc
control-plane drill tương ứng.

## Ma trận số máy tối thiểu

| Nhóm | Tối thiểu | Khuyến nghị |
|---|---:|---:|
| Inventory, CPU, RAM, local/block storage, GPU | 1 | 2–3 instance cùng SKU |
| Network, web, database client-server | 2 agent + Controller | 3 để có generator riêng |
| Replication, failover, load balancer | 3 agent + Controller | 4 để tách generator |
| Cross-zone/cross-region, DR | 2–3 agent ở vị trí khác nhau | lặp lại nhiều time window |

## Thứ tự triển khai đề xuất

1. Hoàn thiện M1 và schema time series vì storage là executor đang trưởng thành
   nhất và là tiêu chí ưu tiên triển khai đầu tiên.
2. Bật M2 sau khi mTLS, watchdog và generator saturation guard hoàn tất.
3. Thêm M3 để tách giới hạn compute/memory khỏi storage/network.
4. Xây M4 trên runner đã ổn định.
5. Bổ sung adapter provider và các drill M5.
6. Chỉ khóa ngưỡng suitability/provider score ở M6 sau khi có tập dữ liệu thực
   từ nhiều cloud Việt Nam, cloud quốc tế và hạ tầng bare-metal tự vận hành.
