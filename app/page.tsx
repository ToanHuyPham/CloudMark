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
  time_series?: {
    interval_ms: number;
    bandwidth: { elapsed_ms: number; value: number; direction: string }[];
    iops: { elapsed_ms: number; value: number; direction: string }[];
    latency: { elapsed_ms: number; value: number; direction: string }[];
  };
};

type Run = {
  id: string;
  suite: string;
  profile: string;
  status: string;
  started_at?: string;
  finished_at?: string;
  error?: string;
  progress?: number;
  phase?: string;
  current_job?: string;
  completed_steps?: number;
  total_steps?: number;
  cancel_requested?: boolean;
  runner_version?: string;
  methodology_version?: string;
  tool_version?: string;
  result?: { jobs?: StorageMetric[] };
};

type Scenario = { id: string; label: string; status: "available" | "partial" | "roadmap"; primary: string; coverage: string };
type AssessmentDomain = { id: string; label: string; status: "available" | "partial" | "roadmap"; summary: string };

type Dashboard = {
  version: string;
  system: { inventory: Inventory; provider: Provider };
  runs: Run[];
  profiles: {
    storage: Record<string, { label: string; description: string; estimated_minutes: number; profile_version: string; methodology_version: string; jobs: { name: string }[] }>;
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
  if (!model) return "Detecting system";
  return model.replace(/\(R\)|\(TM\)|CPU|Processor/gi, "").replace(/\s+/g, " ").trim();
}

function statusLabel(status: string) {
  return {
    queued: "Queued",
    running: "Running",
    completed: "Completed",
    failed: "Failed",
    cancelled: "Cancelled",
  }[status] || status;
}

function coverageLabel(status: Scenario["status"]) {
  return {
    available: "Available",
    partial: "Partially supported",
    roadmap: "Planned",
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
  const [selectedStorageProfile, setSelectedStorageProfile] = useState("disk-quick");
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
  const activeStorage = dashboard?.runs.find(
    (run) => run.suite === "storage" && ["queued", "running"].includes(run.status),
  );
  const latestStorage = dashboard?.runs.find(
    (run) => run.suite === "storage" && run.status === "completed" && run.result?.jobs?.length,
  );
  const selectedProfile = dashboard?.profiles.storage[selectedStorageProfile];
  const storageJobs = useMemo(() => latestStorage?.result?.jobs || [], [latestStorage]);
  const maxStorage = useMemo(
    () => Math.max(1, ...storageJobs.map((job) => Math.max(job.read.iops || 0, job.write.iops || 0))),
    [storageJobs],
  );
  const bandwidthTimeline = useMemo(() => {
    const job = storageJobs[storageJobs.length - 1];
    const points = job?.time_series?.bandwidth || [];
    const stride = Math.max(1, Math.ceil(points.length / 90));
    return points.filter((_, index) => index % stride === 0);
  }, [storageJobs]);
  const maxTimelineBandwidth = useMemo(
    () => Math.max(1, ...bandwidthTimeline.map((point) => point.value)),
    [bandwidthTimeline],
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
      if (!response.ok) throw new Error("Unable to rescan the system");
      await loadDashboard();
      setNotice("System inventory and cloud detection have been updated.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to connect to the API");
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

  async function startStorage() {
    if (!requireToken()) return;
    if (!inventory?.capabilities.fio) {
      setNotice("fio is not installed. Run CloudMark bootstrap --packs storage first.");
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      const response = await fetch(`${API}/runs`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CloudMark-Token": token },
        body: JSON.stringify({ suite: "storage", profile: selectedStorageProfile, confirm_write: true }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Unable to start the benchmark");
      setNotice(`Created ${payload.id} with the ${selectedProfile?.label || selectedStorageProfile} profile. The temporary file will be removed after the run.`);
      await loadDashboard();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Benchmark failed");
    } finally {
      setBusy(false);
    }
  }

  async function cancelStorage() {
    if (!activeStorage || !requireToken()) return;
    setBusy(true);
    try {
      const response = await fetch(`${API}/runs/${activeStorage.id}/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CloudMark-Token": token },
        body: "{}",
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Unable to cancel the benchmark");
      setNotice(`Cancellation requested for ${activeStorage.id}. Cleanup is in progress.`);
      await loadDashboard();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to cancel the benchmark");
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
      if (!response.ok) throw new Error(payload.error || "Unable to create a pairing session");
      setPairing(payload);
      setNotice("A 30-minute pairing session is ready for two cloud agents.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to create the pairing session");
    } finally {
      setBusy(false);
    }
  }

  function saveToken() {
    sessionStorage.setItem("cloudmark-controller-token", token.trim());
    setToken(token.trim());
    setTokenOpen(false);
    setNotice("Controller access is enabled for this browser session.");
  }

  const nav = [
    ["overview", "Overview", "01"],
    ["catalog", "Assessment Catalog", "02"],
    ["storage", "Storage Assessment", "03"],
    ["network", "Distributed Testing", "04"],
    ["scenarios", "Workload Suitability", "05"],
    ["history", "History", "06"],
  ];

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">CM</span>
          <span><strong>CloudMark</strong><small>infrastructure intelligence</small></span>
        </div>
        <nav aria-label="Dashboard navigation">
          {nav.map(([id, label, number]) => (
            <button key={id} className={activeView === id ? "nav-item active" : "nav-item"} onClick={() => setActiveView(id)}>
              <span>{number}</span>{label}
            </button>
          ))}
        </nav>
        <div className="sidebar-policy">
          <span className="policy-dot" />
          <div><strong>Private by default</strong><small>No automatic result uploads</small></div>
        </div>
        <div className="version">CORE / {dashboard?.version || "0.2.0"}</div>
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
            <button className="button primary" onClick={refreshSystem} disabled={busy}>{busy ? "Processing" : "Rescan"}</button>
          </div>
        </header>

        {notice && <div className="notice"><span>●</span>{notice}<button onClick={() => setNotice(null)} aria-label="Dismiss notification">×</button></div>}

        {activeView === "overview" && (
          <div className="view overview-view">
            <section className="hero-panel">
              <div className="hero-copy">
                <div className="provider-line">
                  <span className={(provider?.confidence || 0) < 0.95 ? "provider-badge unknown" : "provider-badge"}>
                    {(provider?.confidence || 0) >= 0.95 ? "METADATA VERIFIED" : provider?.confidence ? "DECLARED ENVIRONMENT" : "UNVERIFIED ENVIRONMENT"}
                  </span>
                  <span>{provider?.source || "Searching for trusted metadata evidence"}</span>
                </div>
                <h2>{provider?.provider === "Unknown" ? "Local or bare-metal system without a verified provider" : provider?.provider}</h2>
                <p>
                  {shortCpu(inventory?.cpu.model)} · {inventory?.cpu.logical_cores || "—"} logical cores · {formatBytes(inventory?.memory.total_bytes, true)} RAM
                </p>
                <div className="hero-meta">
                  <div><small>INSTANCE</small><strong>{provider?.instance_type || "Not identified"}</strong></div>
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
                <p>{formatBytes(primaryDisk?.free_bytes, true)} free · {primaryDisk?.health || "Unknown"}</p>
              </article>
              <article className="metric-card accent-violet">
                <span className="metric-index">NETWORK / 04</span>
                <strong>{inventory?.network.addresses?.length || 0}<small>local paths</small></strong>
                <p>Cloud → controller test disabled</p>
              </article>
            </section>

            <section className="dashboard-grid">
              <article className="panel storage-summary">
                <div className="panel-head"><div><span className="section-kicker">PRIORITY ASSESSMENT</span><h3>Storage capability</h3></div><button className="text-button" onClick={() => setActiveView("storage")}>Open storage assessment →</button></div>
                <div className="storage-content">
                  <div className="disk-visual"><div className="disk-core"><span>{primaryDisk?.name || "DISK"}</span><strong>{inventory?.capabilities.fio ? "FIO AVAILABLE" : "INSTALL FIO"}</strong></div></div>
                  <div className="check-list">
                    <div><span className="ok">✓</span><p><strong>Filesystem-safe</strong><small>Temporary files only, never raw devices</small></p></div>
                    <div><span className={inventory?.capabilities.fio ? "ok" : "warn"}>{inventory?.capabilities.fio ? "✓" : "!"}</span><p><strong>fio runtime</strong><small>{inventory?.capabilities.fio ? "Ready" : "Storage pack bootstrap required"}</small></p></div>
                    <div><span className="ok">✓</span><p><strong>Latency percentiles</strong><small>P50 / P95 / P99 / P99.9</small></p></div>
                  </div>
                </div>
              </article>

              <article className="panel scenario-summary">
                <div className="panel-head"><div><span className="section-kicker">ASSESSMENT CATALOG</span><h3>{domainCounts.total || 17} technical domains</h3></div><button className="text-button" onClick={() => setActiveView("catalog")}>View all →</button></div>
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
              <div><span className="section-kicker">FULL-STACK INFRASTRUCTURE COVERAGE</span><h2>From hardware and hypervisors to workloads, operations, and the control plane.</h2><p>CloudMark organizes evidence into independent technical domains before mapping it to intended use. Modules without complete executors are explicitly marked as Partially supported or Planned.</p></div>
            </section>
            <section className="coverage-strip" aria-label="Assessment catalog status">
              <article className="coverage-stat available"><span>AVAILABLE</span><strong>{domainCounts.available}</strong><small>ready to collect or execute</small></article>
              <article className="coverage-stat partial"><span>PARTIAL</span><strong>{domainCounts.partial}</strong><small>some evidence is available</small></article>
              <article className="coverage-stat roadmap"><span>ROADMAP</span><strong>{domainCounts.roadmap}</strong><small>not included in scoring</small></article>
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
              <div><span className="section-kicker">CURRENT AVAILABLE EXECUTOR</span><h2>Measure storage by workload, not by a single MB/s number.</h2><p>Five profiles cover short validation, general-purpose, database, large-block throughput, and sustained behavior using safe 512 MiB–8 GiB temporary files.</p></div>
              <div className="runner-actions">
                <label><span>PROFILE</span><select value={selectedStorageProfile} onChange={(event) => setSelectedStorageProfile(event.target.value)} disabled={Boolean(activeStorage)}>{Object.entries(dashboard?.profiles.storage || {}).map(([id, profile]) => <option key={id} value={id}>{profile.label} · ≈ {profile.estimated_minutes} min</option>)}</select></label>
                <button className="button primary" onClick={startStorage} disabled={busy || Boolean(activeStorage)}>Run assessment</button>
              </div>
            </section>
            {activeStorage && (
              <section className="panel run-progress" aria-live="polite">
                <div><span className="section-kicker">ACTIVE RUN / {activeStorage.id}</span><strong>{activeStorage.current_job || activeStorage.phase || "Starting"}</strong><small>{activeStorage.completed_steps || 0} of {activeStorage.total_steps || 1} steps · {Math.round((activeStorage.progress || 0) * 100)}%</small></div>
                <div className="progress-track"><i style={{ width: `${Math.max(2, (activeStorage.progress || 0) * 100)}%` }} /></div>
                <button className="button danger" onClick={cancelStorage} disabled={busy || activeStorage.cancel_requested}>{activeStorage.cancel_requested ? "Cancelling" : "Cancel run"}</button>
              </section>
            )}
            <section className="storage-layout">
              <article className="panel chart-panel">
                <div className="panel-head"><div><span className="section-kicker">LATEST MEASUREMENT</span><h3>IOPS by workload</h3></div><span className="run-id">{latestStorage?.id || "NO RUN YET"}</span></div>
                {storageJobs.length ? (
                  <div className="bar-chart">
                    {storageJobs.map((job) => {
                      const value = Math.max(job.read.iops || 0, job.write.iops || 0);
                      return <div className="bar-row" key={job.name}><span>{job.name}</span><div><i style={{ width: `${Math.max(3, (value / maxStorage) * 100)}%` }} /></div><strong>{Math.round(value).toLocaleString()} IOPS</strong></div>;
                    })}
                  </div>
                ) : (
                  <div className="empty-chart"><div className="chart-grid" /><strong>No fio results yet</strong><p>Bootstrap the storage pack, then run Disk Quick to create the first baseline.</p></div>
                )}
              </article>
              <article className="panel profile-panel">
                <div className="panel-head"><div><span className="section-kicker">{selectedStorageProfile.toUpperCase()}</span><h3>Test matrix</h3></div><span className="duration">≈ {selectedProfile?.estimated_minutes || "—"} min</span></div>
                <p className="profile-description">{selectedProfile?.description}</p>
                <div className="profile-jobs">
                  {selectedProfile?.jobs.map((job, index) => <div key={job.name}><span>{String(index + 1).padStart(2, "0")}</span><strong>{job.name}</strong><small>filesystem-safe</small></div>)}
                </div>
                <div className="safety-note"><strong>Safety gate</strong><p>Raw-device access, TRIM, and destructive preconditioning are disabled in the default configuration.</p></div>
              </article>
            </section>
            <section className="panel timeline-panel">
              <div className="panel-head"><div><span className="section-kicker">ONE-SECOND TELEMETRY</span><h3>Bandwidth stability</h3></div><span className="run-id">{storageJobs[storageJobs.length - 1]?.name || "NO TIME SERIES"}</span></div>
              {bandwidthTimeline.length ? (
                <div className="timeline-content">
                  <div className="timeline-chart" aria-label="One-second storage bandwidth samples">
                    {bandwidthTimeline.map((point, index) => <i key={`${point.elapsed_ms}-${point.direction}-${index}`} className={point.direction} style={{ height: `${Math.max(3, (point.value / maxTimelineBandwidth) * 100)}%` }} title={`${Math.round(point.elapsed_ms / 1000)}s · ${point.direction} · ${formatBytes(point.value)}/s`} />)}
                  </div>
                  <div className="timeline-legend"><span><i className="read" />READ</span><span><i className="write" />WRITE</span><strong>Peak {formatBytes(maxTimelineBandwidth)}/s</strong></div>
                </div>
              ) : <div className="timeline-empty">Run any 0.2.0 storage profile to capture one-second bandwidth, IOPS, and latency evidence.</div>}
            </section>
          </div>
        )}

        {activeView === "network" && (
          <div className="view network-view">
            <section className="section-intro"><div><span className="section-kicker">DISTRIBUTED ASSESSMENT</span><h2>Keep the control path separate from benchmark data traffic.</h2><p>The Controller registers systems and stores evidence; performance traffic flows directly between provider agents. The automated network executor is not enabled in the current release.</p></div><button className="button primary" onClick={createPairing} disabled={busy}>Create pairing session</button></section>
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
            {pairing && <section className="pairing-card"><div><span>ASSESSMENT SESSION</span><strong>{pairing.id}</strong><small>Expires {new Date(pairing.expires_at).toLocaleTimeString("en-US")}</small></div><code>{pairing.join_token}</code></section>}
            <section className="network-checks">
              {[["TCP", "1 / 4 / 8 / 16 streams"], ["UDP", "jitter · loss · rate sweep"], ["LATENCY", "idle · loaded · bufferbloat"], ["DIRECTION", "A→B · B→A · bidirectional"]].map(([name, detail]) => <article key={name}><span>{name}</span><strong>{detail}</strong><small>Network executor scope</small></article>)}
            </section>
          </div>
        )}

        {activeView === "scenarios" && (
          <div className="view scenarios-view">
            <section className="section-intro"><div><span className="section-kicker">SUITABILITY COVERAGE</span><h2>Turn full-stack evidence into fit-for-purpose recommendations.</h2><p>Twelve use cases aggregate evidence from {domainCounts.total || 17} technical domains. CloudMark does not score a workload without an executor or the required evidence; {scenarioCounts.available} use case is currently available and {scenarioCounts.partial} have partial evidence.</p></div></section>
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
            <section className="section-intro"><div><span className="section-kicker">IMMUTABLE RAW RESULTS</span><h2>Assessment history on this Controller.</h2><p>Raw metrics remain in SQLite so results can be recalculated when the methodology changes.</p></div></section>
            <section className="panel history-table">
              <div className="table-head"><span>RUN</span><span>SUITE / PROFILE</span><span>STATUS</span><span>STARTED</span></div>
              {dashboard?.runs.length ? dashboard.runs.map((run) => (
                <div className="table-row" key={run.id}><code>{run.id}</code><span>{run.suite} / {run.profile}</span><span className={`run-status ${run.status}`}>{statusLabel(run.status)}{run.status === "running" ? ` · ${Math.round((run.progress || 0) * 100)}%` : ""}</span><span>{run.started_at ? new Date(run.started_at).toLocaleString("en-US") : "—"}</span></div>
              )) : <div className="empty-row">No benchmark runs yet. Current inventory is still read directly from the API.</div>}
            </section>
          </div>
        )}

        <footer className="app-footer"><span>CLOUDMARK / EVIDENCE-DRIVEN</span><span>RESULTS STORED LOCALLY</span><span>CLOUD → CONTROLLER OFF</span></footer>
      </section>

      {tokenOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setTokenOpen(false)}>
          <div className="modal" role="dialog" aria-modal="true" aria-labelledby="token-title" onMouseDown={(event) => event.stopPropagation()}>
            <span className="section-kicker">LOCAL AUTHORIZATION</span><h2 id="token-title">Controller key</h2><p>Paste the token printed by <code>cloudmark serve</code>. It is retained only for this browser-tab session.</p>
            <input autoFocus type="password" value={token} onChange={(event) => setToken(event.target.value)} placeholder="CloudMark controller token" />
            <div className="modal-actions"><button className="button secondary" onClick={() => setTokenOpen(false)}>Cancel</button><button className="button primary" onClick={saveToken}>Connect</button></div>
          </div>
        </div>
      )}
    </main>
  );
}
