"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

const API = "http://127.0.0.1:8787/api/v1";

type Disk = {
  name: string;
  filesystem?: string;
  size_bytes?: number;
  free_bytes?: number;
  health?: string;
};

type Inventory = {
  hostname: string;
  os: { system: string; release: string; distribution: string; architecture: string };
  cpu: { model: string; logical_cores: number };
  memory: { total_bytes?: number };
  virtualization: { type: string; technology?: string };
  disks: Disk[];
  network: { addresses: { family: string; address: string }[] };
  capabilities: Record<string, boolean>;
};

type Provider = {
  provider: string;
  confidence: number;
  source: string;
  region?: string;
  zone?: string;
  instance_type?: string;
  evidence: string[];
};

type StorageMetric = {
  name: string;
  read: { iops: number; bandwidth_bytes_per_second: number; p50_ms?: number; p90_ms?: number; p99_ms?: number };
  write: { iops: number; bandwidth_bytes_per_second: number; p50_ms?: number; p90_ms?: number; p99_ms?: number };
};

type Run = {
  id: string;
  suite: string;
  profile: string;
  status: string;
  started_at?: string;
  finished_at?: string;
  error?: string;
  result?: { jobs?: StorageMetric[] };
};

type Scenario = { id: string; label: string; status: "available" | "partial" | "roadmap"; primary: string; coverage: string };
type AssessmentDomain = { id: string; label: string; status: "available" | "partial" | "roadmap"; summary: string };

type Dashboard = {
  version: string;
  system: { inventory: Inventory; provider: Provider };
  runs: Run[];
  profiles: {
    storage: Record<string, { label: string; description: string; estimated_minutes: number; jobs: { name: string }[] }>;
    network: Record<string, { label: string; description: string; requires_agents: number }>;
    domains: AssessmentDomain[];
    scenarios: Scenario[];
  };
  policy: {
    cloud_to_controller_network_test: boolean;
    provider_internal_peer_test: boolean;
    raw_device_test: boolean;
  };
};

function formatBytes(value?: number, compact = false) {
  if (!value && value !== 0) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let current = value;
  let index = 0;
  while (current >= 1024 && index < units.length - 1) {
    current /= 1024;
    index += 1;
  }
  return `${current.toFixed(compact ? 0 : 1)} ${units[index]}`;
}

function shortCpu(model?: string) {
  if (!model) return "Đang nhận diện";
  return model.replace(/\(R\)|\(TM\)|CPU|Processor/gi, "").replace(/\s+/g, " ").trim();
}

function statusLabel(status: string) {
  return {
    queued: "Đang chờ",
    running: "Đang chạy",
    completed: "Hoàn tất",
    failed: "Thất bại",
  }[status] || status;
}

function coverageLabel(status: Scenario["status"]) {
  return {
    available: "Đã hỗ trợ",
    partial: "Hỗ trợ một phần",
    roadmap: "Theo lộ trình",
  }[status];
}

export default function Home() {
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [apiState, setApiState] = useState<"loading" | "online" | "offline">("loading");
  const [activeView, setActiveView] = useState("overview");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [token, setToken] = useState(() =>
    typeof window === "undefined" ? "" : sessionStorage.getItem("cloudmark-controller-token") || "",
  );
  const [tokenOpen, setTokenOpen] = useState(false);
  const [pairing, setPairing] = useState<{ id: string; join_token: string; expires_at: string } | null>(null);

  const loadDashboard = useCallback(async () => {
    try {
      const response = await fetch(`${API}/dashboard`, { cache: "no-store" });
      if (!response.ok) throw new Error("API unavailable");
      setDashboard(await response.json());
      setApiState("online");
    } catch {
      setApiState("offline");
    }
  }, []);

  useEffect(() => {
    const startup = window.setTimeout(loadDashboard, 0);
    const timer = window.setInterval(loadDashboard, 5000);
    return () => {
      window.clearTimeout(startup);
      window.clearInterval(timer);
    };
  }, [loadDashboard]);

  const inventory = dashboard?.system.inventory;
  const provider = dashboard?.system.provider;
  const primaryDisk = inventory?.disks?.[0];
  const latestStorage = dashboard?.runs.find(
    (run) => run.suite === "storage" && run.status === "completed" && run.result?.jobs?.length,
  );
  const storageJobs = useMemo(() => latestStorage?.result?.jobs || [], [latestStorage]);
  const maxStorage = useMemo(
    () => Math.max(1, ...storageJobs.map((job) => Math.max(job.read.iops || 0, job.write.iops || 0))),
    [storageJobs],
  );
  const evidenceReadiness = useMemo(() => {
    if (!inventory) return 0;
    const readyTools = Object.values(inventory.capabilities).filter(Boolean).length;
    const inventoryEvidence = 28;
    const toolEvidence = readyTools * 9;
    const providerEvidence = Math.round((provider?.confidence || 0) * 18);
    const benchmarkEvidence = dashboard?.runs.some((run) => run.status === "completed") ? 18 : 0;
    return Math.min(100, inventoryEvidence + toolEvidence + providerEvidence + benchmarkEvidence);
  }, [dashboard?.runs, inventory, provider?.confidence]);
  const scenarioCounts = useMemo(() => {
    const scenarios = dashboard?.profiles.scenarios || [];
    return {
      available: scenarios.filter((scenario) => scenario.status === "available").length,
      partial: scenarios.filter((scenario) => scenario.status === "partial").length,
    };
  }, [dashboard?.profiles.scenarios]);
  const domainCounts = useMemo(() => {
    const domains = dashboard?.profiles.domains || [];
    return {
      total: domains.length,
      available: domains.filter((domain) => domain.status === "available").length,
      partial: domains.filter((domain) => domain.status === "partial").length,
      roadmap: domains.filter((domain) => domain.status === "roadmap").length,
    };
  }, [dashboard?.profiles.domains]);

  async function refreshSystem() {
    setBusy(true);
    setNotice(null);
    try {
      const response = await fetch(`${API}/system?refresh=true`, { cache: "no-store" });
      if (!response.ok) throw new Error("Không thể quét lại hệ thống");
      await loadDashboard();
      setNotice("Đã cập nhật inventory và nhận diện cloud.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Không thể kết nối API");
    } finally {
      setBusy(false);
    }
  }

  function requireToken() {
    if (!token) {
      setTokenOpen(true);
      return false;
    }
    return true;
  }

  async function startDiskQuick() {
    if (!requireToken()) return;
    if (!inventory?.capabilities.fio) {
      setNotice("Chưa có fio. Hãy chạy CloudMark bootstrap --packs storage trước.");
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      const response = await fetch(`${API}/runs`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CloudMark-Token": token },
        body: JSON.stringify({ suite: "storage", profile: "disk-quick", confirm_write: true }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Không thể bắt đầu benchmark");
      setNotice(`Đã tạo ${payload.id}. File tạm sẽ được xóa sau khi chạy.`);
      await loadDashboard();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Benchmark thất bại");
    } finally {
      setBusy(false);
    }
  }

  async function createPairing() {
    if (!requireToken()) return;
    setBusy(true);
    try {
      const response = await fetch(`${API}/sessions`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CloudMark-Token": token },
        body: JSON.stringify({ label: "Provider internal network assessment" }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Không thể tạo phiên pairing");
      setPairing(payload);
      setNotice("Phiên pairing 30 phút đã sẵn sàng cho hai cloud agent.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Không thể tạo pairing");
    } finally {
      setBusy(false);
    }
  }

  function saveToken() {
    sessionStorage.setItem("cloudmark-controller-token", token.trim());
    setToken(token.trim());
    setTokenOpen(false);
    setNotice("Đã kết nối quyền điều khiển trong phiên trình duyệt này.");
  }

  const nav = [
    ["overview", "Tổng quan", "01"],
    ["catalog", "Danh mục đánh giá", "02"],
    ["storage", "Đánh giá Storage", "03"],
    ["network", "Kiểm thử phân tán", "04"],
    ["scenarios", "Mức độ phù hợp", "05"],
    ["history", "Lịch sử", "06"],
  ];

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">CM</span>
          <span><strong>CloudMark</strong><small>infrastructure intelligence</small></span>
        </div>
        <nav aria-label="Điều hướng dashboard">
          {nav.map(([id, label, number]) => (
            <button key={id} className={activeView === id ? "nav-item active" : "nav-item"} onClick={() => setActiveView(id)}>
              <span>{number}</span>{label}
            </button>
          ))}
        </nav>
        <div className="sidebar-policy">
          <span className="policy-dot" />
          <div><strong>Private by default</strong><small>Không upload kết quả tự động</small></div>
        </div>
        <div className="version">CORE / {dashboard?.version || "0.1.0"}</div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <div className="eyebrow">LOCAL CONTROLLER / {inventory?.hostname || "WAITING"}</div>
            <h1>{activeView === "overview" ? "Infrastructure assessment" : nav.find(([id]) => id === activeView)?.[1]}</h1>
          </div>
          <div className="top-actions">
            <span className={`api-state ${apiState}`}><i />API {apiState === "online" ? "online" : apiState === "offline" ? "offline" : "checking"}</span>
            <button className="button secondary" onClick={() => setTokenOpen(true)}>Controller key</button>
            <button className="button primary" onClick={refreshSystem} disabled={busy}>{busy ? "Đang xử lý" : "Quét lại"}</button>
          </div>
        </header>

        {notice && <div className="notice"><span>●</span>{notice}<button onClick={() => setNotice(null)} aria-label="Đóng thông báo">×</button></div>}

        {activeView === "overview" && (
          <div className="view overview-view">
            <section className="hero-panel">
              <div className="hero-copy">
                <div className="provider-line">
                  <span className={(provider?.confidence || 0) < 0.95 ? "provider-badge unknown" : "provider-badge"}>
                    {(provider?.confidence || 0) >= 0.95 ? "METADATA VERIFIED" : provider?.confidence ? "DECLARED ENVIRONMENT" : "UNVERIFIED ENVIRONMENT"}
                  </span>
                  <span>{provider?.source || "Đang tìm bằng chứng metadata"}</span>
                </div>
                <h2>{provider?.provider === "Unknown" ? "Máy local / bare-metal chưa gắn provider" : provider?.provider}</h2>
                <p>
                  {shortCpu(inventory?.cpu.model)} · {inventory?.cpu.logical_cores || "—"} logical cores · {formatBytes(inventory?.memory.total_bytes, true)} RAM
                </p>
                <div className="hero-meta">
                  <div><small>INSTANCE</small><strong>{provider?.instance_type || "Chưa xác định"}</strong></div>
                  <div><small>REGION / ZONE</small><strong>{provider?.region || "—"} {provider?.zone || ""}</strong></div>
                  <div><small>CONFIDENCE</small><strong>{Math.round((provider?.confidence || 0) * 100)}%</strong></div>
                </div>
              </div>
              <div className="readiness-ring" style={{ "--value": evidenceReadiness } as React.CSSProperties}>
                <div><strong>{inventory ? evidenceReadiness : "—"}</strong><span>%</span><small>evidence coverage</small></div>
              </div>
            </section>

            <section className="metric-grid">
              <article className="metric-card accent-lime">
                <span className="metric-index">CPU / 01</span>
                <strong>{inventory?.cpu.logical_cores || "—"}<small>threads</small></strong>
                <p>{shortCpu(inventory?.cpu.model)}</p>
              </article>
              <article className="metric-card accent-cyan">
                <span className="metric-index">MEMORY / 02</span>
                <strong>{formatBytes(inventory?.memory.total_bytes, true)}<small>installed</small></strong>
                <p>{inventory?.virtualization.type || "unknown"} environment</p>
              </article>
              <article className="metric-card accent-orange">
                <span className="metric-index">STORAGE / 03</span>
                <strong>{formatBytes(primaryDisk?.size_bytes, true)}<small>{primaryDisk?.filesystem || "volume"}</small></strong>
                <p>{formatBytes(primaryDisk?.free_bytes, true)} còn trống · {primaryDisk?.health || "Unknown"}</p>
              </article>
              <article className="metric-card accent-violet">
                <span className="metric-index">NETWORK / 04</span>
                <strong>{inventory?.network.addresses?.length || 0}<small>local paths</small></strong>
                <p>Cloud → controller test disabled</p>
              </article>
            </section>

            <section className="dashboard-grid">
              <article className="panel storage-summary">
                <div className="panel-head"><div><span className="section-kicker">PRIORITY ASSESSMENT</span><h3>Năng lực lưu trữ</h3></div><button className="text-button" onClick={() => setActiveView("storage")}>Mở đánh giá Storage →</button></div>
                <div className="storage-content">
                  <div className="disk-visual"><div className="disk-core"><span>{primaryDisk?.name || "DISK"}</span><strong>{inventory?.capabilities.fio ? "FIO AVAILABLE" : "INSTALL FIO"}</strong></div></div>
                  <div className="check-list">
                    <div><span className="ok">✓</span><p><strong>Filesystem-safe</strong><small>Chỉ dùng file tạm, không raw device</small></p></div>
                    <div><span className={inventory?.capabilities.fio ? "ok" : "warn"}>{inventory?.capabilities.fio ? "✓" : "!"}</span><p><strong>fio runtime</strong><small>{inventory?.capabilities.fio ? "Đã sẵn sàng" : "Cần bootstrap storage pack"}</small></p></div>
                    <div><span className="ok">✓</span><p><strong>Latency percentiles</strong><small>P50 / P95 / P99 / P99.9</small></p></div>
                  </div>
                </div>
              </article>

              <article className="panel scenario-summary">
                <div className="panel-head"><div><span className="section-kicker">ASSESSMENT CATALOG</span><h3>{domainCounts.total || 17} miền kỹ thuật</h3></div><button className="text-button" onClick={() => setActiveView("catalog")}>Xem toàn bộ →</button></div>
                <div className="mini-scenarios">
                  {dashboard?.profiles.domains.slice(0, 6).map((domain) => (
                    <div key={domain.id}><span className={domain.status} /><p>{domain.label}</p><small>{coverageLabel(domain.status)}</small></div>
                  ))}
                </div>
              </article>
            </section>
          </div>
        )}

        {activeView === "catalog" && (
          <div className="view catalog-view">
            <section className="section-intro">
              <div><span className="section-kicker">FULL-STACK INFRASTRUCTURE COVERAGE</span><h2>Từ phần cứng và hypervisor đến workload, vận hành và control plane.</h2><p>CloudMark tổ chức bằng chứng thành các miền kỹ thuật độc lập, sau đó mới ánh xạ sang mục đích sử dụng. Module chưa đủ executor được ghi rõ là Hỗ trợ một phần hoặc Theo lộ trình.</p></div>
            </section>
            <section className="coverage-strip" aria-label="Trạng thái danh mục đánh giá">
              <article className="coverage-stat available"><span>AVAILABLE</span><strong>{domainCounts.available}</strong><small>có thể thu thập hoặc chạy ngay</small></article>
              <article className="coverage-stat partial"><span>PARTIAL</span><strong>{domainCounts.partial}</strong><small>đã có một phần bằng chứng</small></article>
              <article className="coverage-stat roadmap"><span>ROADMAP</span><strong>{domainCounts.roadmap}</strong><small>chưa dùng để chấm điểm</small></article>
            </section>
            <section className="domain-grid">
              {dashboard?.profiles.domains.map((domain, index) => (
                <article key={domain.id} className={`domain-card ${domain.status}`}>
                  <div><span>{String(index + 1).padStart(2, "0")}</span><i className={domain.status} /></div>
                  <h3>{domain.label}</h3><p>{domain.summary}</p>
                  <footer><span>{coverageLabel(domain.status)}</span><strong>DOMAIN</strong></footer>
                </article>
              ))}
            </section>
          </div>
        )}

        {activeView === "storage" && (
          <div className="view storage-view">
            <section className="section-intro">
              <div><span className="section-kicker">CURRENT AVAILABLE EXECUTOR</span><h2>Đo ổ cứng theo workload, không theo một con số MB/s.</h2><p>Storage là một miền trong catalog full-stack. Quick dùng 512 MiB; Standard dùng 4 GiB. Cả hai luôn giữ safety reserve và xóa file tạm sau khi hoàn thành.</p></div>
              <button className="button primary" onClick={startDiskQuick} disabled={busy}>Chạy Disk Quick</button>
            </section>
            <section className="storage-layout">
              <article className="panel chart-panel">
                <div className="panel-head"><div><span className="section-kicker">LATEST MEASUREMENT</span><h3>IOPS theo workload</h3></div><span className="run-id">{latestStorage?.id || "NO RUN YET"}</span></div>
                {storageJobs.length ? (
                  <div className="bar-chart">
                    {storageJobs.map((job) => {
                      const value = Math.max(job.read.iops || 0, job.write.iops || 0);
                      return <div className="bar-row" key={job.name}><span>{job.name}</span><div><i style={{ width: `${Math.max(3, (value / maxStorage) * 100)}%` }} /></div><strong>{Math.round(value).toLocaleString()} IOPS</strong></div>;
                    })}
                  </div>
                ) : (
                  <div className="empty-chart"><div className="chart-grid" /><strong>Chưa có kết quả fio</strong><p>Bootstrap storage pack, sau đó chạy Disk Quick để tạo baseline đầu tiên.</p></div>
                )}
              </article>
              <article className="panel profile-panel">
                <div className="panel-head"><div><span className="section-kicker">DISK QUICK</span><h3>Test matrix</h3></div><span className="duration">≈ 4 min</span></div>
                <div className="profile-jobs">
                  {dashboard?.profiles.storage["disk-quick"]?.jobs.map((job, index) => <div key={job.name}><span>{String(index + 1).padStart(2, "0")}</span><strong>{job.name}</strong><small>filesystem-safe</small></div>)}
                </div>
                <div className="safety-note"><strong>Safety gate</strong><p>Raw device, TRIM và destructive preconditioning bị vô hiệu hóa trong cấu hình mặc định.</p></div>
              </article>
            </section>
          </div>
        )}

        {activeView === "network" && (
          <div className="view network-view">
            <section className="section-intro"><div><span className="section-kicker">DISTRIBUTED ASSESSMENT</span><h2>Tách luồng điều khiển khỏi luồng dữ liệu benchmark.</h2><p>Controller đăng ký máy và lưu bằng chứng; traffic hiệu năng chỉ đi trực tiếp giữa các agent của nhà cung cấp. Network executor tự động chưa được bật trong bản phát hành hiện tại.</p></div><button className="button primary" onClick={createPairing} disabled={busy}>Tạo phiên kết nối</button></section>
            <section className="topology-panel panel">
              <div className="topology-node controller"><span>LOCAL</span><strong>Controller</strong><small>Dashboard + API</small></div>
              <div className="control-line"><span>HTTPS / VPN control</span></div>
              <div className="cloud-boundary">
                <span className="boundary-label">PROVIDER NETWORK</span>
                <div className="topology-node target"><span>VM A</span><strong>Target</strong><small>web · db · storage</small></div>
                <div className="data-line"><i /><span>A ↔ B direct traffic</span></div>
                <div className="topology-node generator"><span>VM B</span><strong>Generator</strong><small>TCP · UDP · load</small></div>
              </div>
              <div className="blocked-line"><span>×</span><p><strong>Cloud → controller measurement</strong><small>Disabled by project policy</small></p></div>
            </section>
            {pairing && <section className="pairing-card"><div><span>ASSESSMENT SESSION</span><strong>{pairing.id}</strong><small>Hết hạn {new Date(pairing.expires_at).toLocaleTimeString("vi-VN")}</small></div><code>{pairing.join_token}</code></section>}
            <section className="network-checks">
              {[["TCP", "1 / 4 / 8 / 16 streams"], ["UDP", "jitter · loss · rate sweep"], ["LATENCY", "idle · loaded · bufferbloat"], ["DIRECTION", "A→B · B→A · bidirectional"]].map(([name, detail]) => <article key={name}><span>{name}</span><strong>{detail}</strong><small>Phạm vi network executor</small></article>)}
            </section>
          </div>
        )}

        {activeView === "scenarios" && (
          <div className="view scenarios-view">
            <section className="section-intro"><div><span className="section-kicker">SUITABILITY COVERAGE</span><h2>Biến bằng chứng full-stack thành khuyến nghị đúng mục đích.</h2><p>12 nhóm nhu cầu được tổng hợp từ {domainCounts.total || 17} miền kỹ thuật. CloudMark không chấm điểm cho workload khi chưa có executor hoặc chưa đủ bằng chứng bắt buộc; hiện có {scenarioCounts.available} nhóm đã hỗ trợ và {scenarioCounts.partial} nhóm có bằng chứng một phần.</p></div></section>
            <section className="scenario-grid">
              {dashboard?.profiles.scenarios.map((scenario, index) => (
                <article key={scenario.id} className={`scenario-card ${scenario.status}`}>
                  <div><span>{String(index + 1).padStart(2, "0")}</span><i className={scenario.status} /></div>
                  <h3>{scenario.label}</h3><p>{scenario.coverage}</p>
                  <footer><span>{coverageLabel(scenario.status)}</span><strong>{scenario.primary}</strong></footer>
                </article>
              ))}
            </section>
          </div>
        )}

        {activeView === "history" && (
          <div className="view history-view">
            <section className="section-intro"><div><span className="section-kicker">IMMUTABLE RAW RESULTS</span><h2>Lịch sử đánh giá trên Controller.</h2><p>Raw metrics được giữ trong SQLite để có thể tính lại kết quả khi methodology thay đổi.</p></div></section>
            <section className="panel history-table">
              <div className="table-head"><span>RUN</span><span>SUITE / PROFILE</span><span>STATUS</span><span>STARTED</span></div>
              {dashboard?.runs.length ? dashboard.runs.map((run) => (
                <div className="table-row" key={run.id}><code>{run.id}</code><span>{run.suite} / {run.profile}</span><span className={`run-status ${run.status}`}>{statusLabel(run.status)}</span><span>{run.started_at ? new Date(run.started_at).toLocaleString("vi-VN") : "—"}</span></div>
              )) : <div className="empty-row">Chưa có benchmark run. Inventory hiện tại vẫn được đọc trực tiếp từ API.</div>}
            </section>
          </div>
        )}

        <footer className="app-footer"><span>CLOUDMARK / EVIDENCE-DRIVEN</span><span>RESULTS STORED LOCALLY</span><span>CLOUD → CONTROLLER OFF</span></footer>
      </section>

      {tokenOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setTokenOpen(false)}>
          <div className="modal" role="dialog" aria-modal="true" aria-labelledby="token-title" onMouseDown={(event) => event.stopPropagation()}>
            <span className="section-kicker">LOCAL AUTHORIZATION</span><h2 id="token-title">Controller key</h2><p>Dán token được in khi chạy <code>cloudmark serve</code>. Token chỉ được giữ trong session của tab này.</p>
            <input autoFocus type="password" value={token} onChange={(event) => setToken(event.target.value)} placeholder="CloudMark controller token" />
            <div className="modal-actions"><button className="button secondary" onClick={() => setTokenOpen(false)}>Hủy</button><button className="button primary" onClick={saveToken}>Kết nối</button></div>
          </div>
        </div>
      )}
    </main>
  );
}
