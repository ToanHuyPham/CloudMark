# Hướng dẫn sử dụng CloudMark

Tài liệu này áp dụng cho `0.1.0-alpha`. Bản alpha đã chạy được inventory, nhận
diện metadata AWS/Azure/GCP, lập kế hoạch bootstrap, chạy `fio` an toàn, lưu lịch
sử SQLite, tạo phiên pairing và hiển thị dashboard local. Network peer executor,
web/database workload và scoring đầy đủ đang ở các milestone tiếp theo.

## 1. Mô hình triển khai

### Chỉ kiểm tra một máy

```text
Controller/dashboard + Agent trên cùng máy test
```

Phù hợp cho inventory, provider detection, CPU/RAM và local/block storage.

### Kiểm tra provider bằng nhiều VM

```text
Máy của bạn: Controller + dashboard
Provider:     VM A (target) ↔ VM B (generator)
Tùy chọn:     VM C (replica/failover)
```

Controller không nhận traffic benchmark từ cloud. Dữ liệu benchmark network sẽ
đi trực tiếp giữa VM A và VM B.

## 2. Yêu cầu

### Máy Controller

- Windows, Linux hoặc macOS;
- Python 3.9 trở lên;
- Node.js 22 trở lên;
- pnpm;
- trình duyệt hiện đại.

### Máy Agent

- Ubuntu/Debian;
- RHEL/CentOS-compatible;
- SLES 12.5/15;
- Windows đang ở mức hỗ trợ inventory alpha;
- quyền `root`, `sudo` hoặc Administrator để bootstrap tool.

Agent không cần Node.js hoặc dashboard.

## 3. Cài trên máy Controller

Clone repository:

```bash
git clone <URL_GITHUB_CUA_DU_AN>
cd CloudMark
```

Tạo Python environment nếu muốn cô lập:

### Linux/macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Cài dashboard:

```bash
pnpm install
```

## 4. Khởi động local

Mở terminal thứ nhất:

```bash
python -m cloudmark serve --data-dir .cloudmark
```

Kết quả:

```text
CloudMark API: http://127.0.0.1:8787/api/v1/health
Controller token: <TOKEN>
Policy: cloud-to-controller network measurement is disabled.
```

Không gửi token lên GitHub hoặc chat công khai.

Mở terminal thứ hai:

```bash
pnpm run dev
```

Mở URL được in ra, thường là `http://localhost:3000`. Nếu port đã dùng, hệ
thống có thể chọn 3001, 3002… Dashboard chấp nhận các port local từ 3000–3010.

Trong dashboard:

1. Chọn **Controller key**.
2. Dán token từ terminal API.
3. Chọn **Kết nối**.
4. Token chỉ tồn tại trong session của tab trình duyệt.

## 5. Inventory

Chạy bằng CLI:

```bash
python -m cloudmark inventory
```

Hoặc chọn **Quét lại** trên dashboard.

Inventory hiện thu thập:

- hostname, OS, kernel và architecture;
- model và số logical CPU;
- tổng RAM;
- volume/disk nhìn thấy từ hệ điều hành;
- IP local;
- virtualization evidence khi hệ điều hành cho phép;
- trạng thái `fio`, `iperf3`, `sysbench`, Docker và Podman.

Cloud detection hiện kiểm tra AWS IMDSv2, Azure IMDS và Google Compute metadata.
Nếu không có bằng chứng tin cậy, kết quả là `Unknown`, không suy đoán từ IP.

Với cloud Việt Nam hoặc cloud tự xây chưa có metadata chuẩn, có thể đặt manifest
theo mẫu `examples/provider-manifest.json` tại `/etc/cloudmark/provider.json`,
`C:\ProgramData\CloudMark\provider.json`, hoặc trỏ biến môi trường
`CLOUDMARK_PROVIDER_MANIFEST`. Manifest chưa ký luôn được ghi là “declared,
unverified” với confidence thấp hơn metadata chính thức.

## 6. Kiểm tra dependency trước khi cài

```bash
python -m cloudmark doctor --packs storage,network,database,web
```

Lệnh này chỉ hiển thị kế hoạch. Nó không thay đổi máy.

Các pack:

| Pack | Nội dung |
|---|---|
| `base` | curl, jq, dmidecode, sysstat, numactl |
| `storage` | fio, smartmontools, nvme-cli |
| `network` | iperf3, ethtool, mtr, DNS tools |
| `database` | sysbench, PostgreSQL, Redis |
| `web` | nginx và HTTP utilities |

## 7. Bootstrap tự động

### Ubuntu/Debian

```bash
sudo python -m cloudmark bootstrap \
  --packs storage,network,database,web \
  --yes
```

### RHEL/CentOS

CloudMark tự nhận diện `dnf`/`yum`:

```bash
sudo python -m cloudmark bootstrap --packs storage,network,database,web --yes
```

Một số package như `sysbench` có thể cần repository bổ sung. Nếu package manager
từ chối, kết quả bootstrap dừng ngay và giữ nguyên log lỗi.

### SLES 12.5/15

```bash
sudo python -m cloudmark bootstrap --packs storage,network,database,web --yes
```

SLES có thể yêu cầu registration hợp lệ. Nếu repository không có tool, sử dụng
offline bundle khi dự án phát hành bundle tương ứng.

Kiểm tra `python3 --version` trước khi cài. CloudMark cần Python 3.9 trở lên;
nếu runtime mặc định của SLES 12.5 thấp hơn, dùng runtime 3.9+ do tổ chức quản lý
hoặc offline bundle thay vì thay thế Python hệ thống.

### Windows

MVP nhận diện `winget`, nhưng chưa tự động ánh xạ toàn bộ `fio`/`iperf3` portable
package. Inventory và Controller chạy được; bootstrap benchmark Windows sẽ được
hoàn thiện ở milestone riêng.

## 8. Chạy storage benchmark

### Preflight

```bash
python -m cloudmark run storage --profile disk-quick
```

Không có `--yes`, CloudMark chỉ kiểm tra:

- `fio` tồn tại;
- đường dẫn test hợp lệ;
- dung lượng trống;
- safety reserve;
- kích thước file sẽ ghi.

### Chạy thật

```bash
python -m cloudmark run storage --profile disk-quick --yes
```

Hoặc mở **Storage Lab** và chọn **Chạy Disk Quick**.

Mặc định:

- file 512 MiB dưới `.cloudmark/benchmark-workspace`;
- sequential read/write;
- random 4 KiB QD1;
- mixed 70/30;
- P50/P90/P95/P99/P99.9;
- file được xóa khi hoàn thành hoặc khi job lỗi.

Standard profile:

```bash
python -m cloudmark run storage --profile disk-standard --yes
```

Standard dùng file 4 GiB và chạy lâu hơn. Không chạy trên máy production đang có
tải nếu muốn kết quả dùng cho so sánh provider.

### Điều CloudMark không làm

- không ghi `/dev/sda`, `/dev/nvme0n1` hoặc raw Windows disk;
- không format volume;
- không chạy TRIM/discard;
- không precondition toàn bộ device;
- không cắt nguồn để kiểm tra PLP.

## 9. Tạo phiên nhiều máy

Trong dashboard mở **Multi-node** → **Tạo phiên pairing**. Hệ thống sinh:

- session ID;
- join token;
- thời hạn 30 phút.

Trên VM A:

```bash
python -m cloudmark join \
  --controller https://CONTROLLER \
  --session SESSION_ID \
  --token JOIN_TOKEN \
  --role target
```

Trên VM B:

```bash
python -m cloudmark join \
  --controller https://CONTROLLER \
  --session SESSION_ID \
  --token JOIN_TOKEN \
  --role generator
```

Nếu Controller chỉ có HTTP trên một VPN/private network đáng tin cậy, thêm
`--allow-http`. Không dùng tùy chọn này qua Internet công cộng.

Trong alpha, agent registration và inventory persistence đã hoạt động. Tự động
chạy `iperf3` trực tiếp A↔B chưa được bật; đây là giới hạn có chủ đích để không
mở một arbitrary network-load endpoint trước khi hoàn tất mTLS và rate limits.

## 10. Đọc dashboard

### Tổng quan

- bằng chứng nhận diện provider;
- CPU, RAM, storage, local network paths;
- tool readiness;
- workload profile coverage.

### Storage Lab

- profile và file size;
- safety state;
- IOPS và latency sau benchmark;
- raw run ID để truy xuất API.

### Multi-node

- topology Controller/Target/Generator;
- chính sách traffic;
- pairing token;
- các phép đo network dự kiến.

### Nhu cầu

Danh sách 12 workload. `Profile ready` nghĩa là profile dữ liệu đã được định
nghĩa; không có nghĩa máy đã đạt yêu cầu. Score chỉ xuất hiện sau khi có đủ raw
evidence.

### Lịch sử

Mỗi run giữ request, trạng thái, thời điểm, lỗi và raw result trong SQLite.

## 11. API nhanh

Health:

```bash
curl http://127.0.0.1:8787/api/v1/health
```

System:

```bash
curl http://127.0.0.1:8787/api/v1/system
```

Inventory run:

```bash
curl -X POST http://127.0.0.1:8787/api/v1/runs \
  -H "Content-Type: application/json" \
  -H "X-CloudMark-Token: TOKEN" \
  -d '{"suite":"inventory","profile":"default"}'
```

## 12. Dữ liệu local

```text
.cloudmark/
├── cloudmark.sqlite3
├── cloudmark.sqlite3-wal
├── cloudmark.sqlite3-shm
├── controller.token
└── benchmark-workspace/
```

Toàn bộ thư mục đã được `.gitignore`.

## 13. Quy trình đánh giá provider khuyến nghị

1. Tạo hai VM trắng cùng SKU, OS và disk type.
2. Cố gắng đặt khác physical host bằng anti-affinity.
3. Bootstrap cùng CloudMark/tool version.
4. Chạy inventory trên cả hai.
5. Chạy disk riêng từng VM, không đồng thời.
6. Chỉ chạy đồng thời khi muốn đo contention.
7. Pair A/B cho network/web/database client-server.
8. Tạo instance mới và lặp lại ở khung giờ khác.
9. Không kết luận toàn provider từ một VM hoặc một lần chạy.

## 14. Troubleshooting

### Dashboard báo API offline

- kiểm tra terminal `cloudmark serve` còn chạy;
- mở `http://127.0.0.1:8787/api/v1/health`;
- kiểm tra port 8787;
- dashboard development chỉ được CORS từ localhost port 3000–3010.

### Storage báo thiếu fio

Chạy `doctor`, sau đó `bootstrap --packs storage --yes` với sudo/root.

### Không đủ dung lượng

Chọn filesystem khác bằng `--workspace`. Không giảm hoặc bỏ safety reserve.

### Provider hiển thị Unknown

Metadata có thể bị tắt, bị firewall chặn hoặc provider không dùng metadata chuẩn.
CloudMark không dùng ASN để tự khẳng định provider. Provider pack Việt Nam và
signed self-hosted manifest sẽ được bổ sung.

### Agent không join được Controller

Controller mặc định bind loopback nên VM bên ngoài không truy cập được. Bản
alpha chỉ nên join qua VPN/reverse proxy HTTPS do bạn kiểm soát. Thiết kế relay
outbound và mTLS enrollment sẽ được bổ sung trước khi bật network executor.

## 15. Kiểm thử dự án

Python:

```bash
python -m unittest discover -s tests_python -v
```

Dashboard production build:

```bash
pnpm run build
```

Không chạy full disk benchmark trong CI dùng chung.
