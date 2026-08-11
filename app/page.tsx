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

type ComputeMetric = {
  name: string;
  threads: number;
  metrics: {
    events: number;
    events_per_second: number;
    elapsed_seconds: number;
    latency: { minimum_ms?: number; average_ms?: number; maximum_ms?: number; p95_ms?: number };
    stability: { mean?: number; minimum?: number; maximum?: number; cv_percent?: number };
  };
  host: { utilization_percent?: number; steal_percent?: number };
};

type MemoryMetric = {
  name: string;
  threads: number;
  metrics: {
    kernel: string;
    elapsed_seconds: number;
    array_bytes: number;
    allocated_bytes: number;
    bandwidth_bytes_per_second: number;
  };
  host: { utilization_percent?: number; steal_percent?: number };
};

type NetworkEndpoint = { id: string; name: string; role: string; address?: string };

type NetworkTcpMeasurement = {
  direction: string;
  sender: NetworkEndpoint;
  receiver: NetworkEndpoint;
  streams: number;
  duration_seconds: number;
  metrics: {
    sent_bits_per_second?: number;
    received_bits_per_second?: number;
    retransmits?: number;
    tcp_rtt_mean_ms?: number;
  };
};

type NetworkLatencyMeasurement = {
  direction: string;
  sender: NetworkEndpoint;
  receiver: NetworkEndpoint;
  metrics: { average_ms?: number; maximum_ms?: number; loss_percent?: number };
};

type NetworkUdpMeasurement = {
  direction: string;
  sender: NetworkEndpoint;
  receiver: NetworkEndpoint;
  target_rate_bps: number;
  rate_fraction_of_tcp_peak: number;
  metrics: { received_bits_per_second?: number; jitter_ms?: number; lost_percent?: number };
};

type NetworkBidirectionalMeasurement = {
  direction: string;
  sender: NetworkEndpoint;
  receiver: NetworkEndpoint;
  streams: number;
  metrics: {
    forward: { received_bits_per_second?: number };
    reverse: { received_bits_per_second?: number };
  };
};

type DatabaseMeasurement = {
  name: string;
  workload: "select-only" | "tpcb-like";
  clients: number;
  threads: number;
  duration_seconds: number;
  warmup_seconds: number;
  connect_per_transaction: boolean;
  metrics: {
    transactions_processed: number;
    failed_transactions: number;
    transactions_per_second: number;
    latency_average_ms: number;
    initial_connection_time_ms?: number;
    tail_latency_status: "unavailable";
    progress: { elapsed_seconds: number; tps: number; latency_average_ms: number; failed: number }[];
  };
  tool: { name: string; version?: string };
};

type WebMeasurement = {
  name: string;
  scheme: "http" | "https";
  path: string;
  concurrency: number;
  duration_seconds: number;
  warmup_seconds: number;
  keep_alive: boolean;
  metrics: {
    complete_requests: number;
    failed_requests: number;
    non_2xx_responses: number;
    successful_requests: number;
    success_percent: number;
    requests_per_second: number;
    time_per_request_ms: number;
    transfer_rate_kib_per_second?: number;
    latency_percentiles_ms: { p50: number; p90: number; p95: number; p99: number; p100: number };
    tls: { status: string; protocol?: string; cipher?: string; raw?: string };
  };
  tool: { name: string; version?: string };
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
  request?: { agent_id?: string; execution?: "controller-host" | "remote-agent" };
  result?: {
    jobs?: StorageMetric[];
    compute_jobs?: ComputeMetric[];
    memory_jobs?: MemoryMetric[];
    scaling?: {
      single_events_per_second: number;
      all_core_events_per_second: number;
      all_core_threads: number;
      efficiency_percent?: number;
    };
    execution?: {
      mode: "remote-agent";
      protocol_version: string;
      agent_version: string;
      agent: { id: string; name: string; role: string; session_id: string };
    };
    measurements?: NetworkTcpMeasurement[];
    latency_measurements?: NetworkLatencyMeasurement[];
    udp_measurements?: NetworkUdpMeasurement[];
    bidirectional_measurements?: NetworkBidirectionalMeasurement[];
    database_measurements?: DatabaseMeasurement[];
    web_measurements?: WebMeasurement[];
    server?: {
      engine?: string;
      scale_factor?: number;
      estimated_dataset_bytes?: number;
      listen_address?: string;
      ports?: { http?: number; https?: number };
      tls?: { protocol?: string; cipher?: string; certificate?: string };
      payloads?: Record<string, number>;
      tools?: { postgres?: string; pgbench?: string; nginx?: string; openssl?: string };
      durability?: Record<string, string | number>;
    };
    cleanup?: { status: string; cleanup_verified?: boolean };
    analysis?: {
      scored: boolean;
      latency_comparison: string;
      directions: {
        direction: string;
        idle_icmp_average_ms?: number;
        idle_icmp_loss_percent?: number;
        loaded_tcp_rtt_mean_ms?: number;
        latency_inflation_ms?: number;
        latency_inflation_percent?: number;
        peak_tcp_received_bits_per_second?: number;
        highest_udp_target_bits_per_second?: number;
        highest_udp_loss_percent?: number;
        highest_udp_jitter_ms?: number;
      }[];
    };
  };
};

type Agent = {
  id: string;
  name: string;
  role: string;
  status: string;
  last_seen_at?: string;
  endpoint: { address?: string };
  system: { inventory?: Inventory; provider?: Provider };
};

type Session = {
  id: string;
  label: string;
  status: string;
  created_at: string;
  expires_at: string;
  agents: Agent[];
};

type Scenario = { id: string; label: string; status: "available" | "partial" | "roadmap"; primary: string; coverage: string };
type AssessmentDomain = { id: string; label: string; status: "available" | "partial" | "roadmap"; summary: string };

type Dashboard = {
  version: string;
  system: { inventory: Inventory; provider: Provider };
  runs: Run[];
  sessions: Session[];
  profiles: {
    compute: Record<string, { label: string; description: string; estimated_minutes: number; profile_version: string; methodology_version: string; jobs: { name: string }[] }>;
    memory: Record<string, { label: string; description: string; estimated_minutes: number; profile_version: string; methodology_version: string; jobs: { name: string }[] }>;
    storage: Record<string, { label: string; description: string; estimated_minutes: number; profile_version: string; methodology_version: string; jobs: { name: string }[] }>;
    network: Record<string, {
      label: string;
      description: string;
      requires_agents: number;
      tcp_streams: number[];
      duration_seconds: number;
      profile_version: string;
      methodology_version: string;
      udp_rate_fractions?: number[];
      bidirectional_streams?: number;
    }>;
    database: Record<string, {
      label: string;
      description: string;
      estimated_minutes: number;
      requires_agents: number;
      engine: string;
      scale_factor: number;
      port: number;
      profile_version: string;
      methodology_version: string;
      jobs: {
        name: string;
        workload: string;
        clients: number;
        threads: number;
        duration: number;
        warmup: number;
        connect_per_transaction?: boolean;
      }[];
    }>;
    web: Record<string, {
      label: string;
      description: string;
      estimated_minutes: number;
      requires_agents: number;
      engine: string;
      http_port: number;
      https_port: number;
      profile_version: string;
      methodology_version: string;
      jobs: {
        name: string;
        scheme: "http" | "https";
        path: string;
        concurrency: number;
        duration: number;
        warmup: number;
        keep_alive: boolean;
      }[];
    }>;
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
  const [selectedComputeProfile, setSelectedComputeProfile] = useState("compute-quick");
  const [selectedMemoryProfile, setSelectedMemoryProfile] = useState("memory-quick");
  const [selectedStorageProfile, setSelectedStorageProfile] = useState("disk-quick");
  const [selectedNetworkProfile, setSelectedNetworkProfile] = useState("network-peer-quick");
  const [selectedDatabaseProfile, setSelectedDatabaseProfile] = useState("postgres-peer-quick");
  const [selectedWebProfile, setSelectedWebProfile] = useState("web-peer-quick");
  const [selectedExecutionTarget, setSelectedExecutionTarget] = useState("local");
  const [selectedSessionId, setSelectedSessionId] = useState("");
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
  const allAgents = dashboard?.sessions.flatMap((session) => session.agents) || [];
  const selectedExecutionAgent = allAgents.find((agent) => agent.id === selectedExecutionTarget);
  const executionInventory = selectedExecutionAgent?.system.inventory || (selectedExecutionTarget === "local" ? inventory : undefined);
  const executionTargetLabel = selectedExecutionAgent?.name || "Controller host";
  const executionTargetOnline = selectedExecutionTarget === "local" || selectedExecutionAgent?.status === "online";
  const memoryReady = executionInventory?.os?.system === "Linux" && Boolean(executionInventory.capabilities?.gcc);
  const primaryDisk = inventory?.disks?.[0];
  const activeStorage = dashboard?.runs.find(
    (run) => run.suite === "storage" && ["queued", "running"].includes(run.status)
      && (run.request?.agent_id || "local") === selectedExecutionTarget,
  );
  const activeSystem = dashboard?.runs.find(
    (run) => ["compute", "memory"].includes(run.suite) && ["queued", "running"].includes(run.status)
      && (run.request?.agent_id || "local") === selectedExecutionTarget,
  );
  const activeLocal = dashboard?.runs.find(
    (run) => ["compute", "memory", "storage"].includes(run.suite) && ["queued", "running"].includes(run.status)
      && (run.request?.agent_id || "local") === selectedExecutionTarget,
  );
  const latestCompute = dashboard?.runs.find(
    (run) => run.suite === "compute" && run.status === "completed" && run.result?.compute_jobs?.length
      && (run.request?.agent_id || "local") === selectedExecutionTarget,
  );
  const latestMemory = dashboard?.runs.find(
    (run) => run.suite === "memory" && run.status === "completed" && run.result?.memory_jobs?.length
      && (run.request?.agent_id || "local") === selectedExecutionTarget,
  );
  const computeJobs = latestCompute?.result?.compute_jobs || [];
  const memoryJobs = latestMemory?.result?.memory_jobs || [];
  const maxComputeRate = Math.max(1, ...computeJobs.map((job) => job.metrics.events_per_second || 0));
  const maxMemoryRate = Math.max(1, ...memoryJobs.map((job) => job.metrics.bandwidth_bytes_per_second || 0));
  const activeNetwork = dashboard?.runs.find(
    (run) => run.suite === "network" && ["queued", "running"].includes(run.status),
  );
  const latestNetwork = dashboard?.runs.find(
    (run) => run.suite === "network" && run.status === "completed" && run.result?.measurements?.length,
  );
  const readySessions = dashboard?.sessions.filter((session) => session.status === "ready") || [];
  const selectedSession = dashboard?.sessions.find((session) => session.id === selectedSessionId)
    || readySessions[0]
    || dashboard?.sessions[0];
  const networkMeasurements = latestNetwork?.result?.measurements || [];
  const latencyMeasurements = latestNetwork?.result?.latency_measurements || [];
  const udpMeasurements = latestNetwork?.result?.udp_measurements || [];
  const bidirectionalMeasurements = latestNetwork?.result?.bidirectional_measurements || [];
  const networkAnalysis = latestNetwork?.result?.analysis?.directions || [];
  const maxNetworkRate = Math.max(1, ...networkMeasurements.map((item) => item.metrics.received_bits_per_second || 0));
  const activeDatabase = dashboard?.runs.find(
    (run) => run.suite === "database" && ["queued", "running"].includes(run.status),
  );
  const latestDatabase = dashboard?.runs.find(
    (run) => run.suite === "database" && run.status === "completed" && run.result?.database_measurements?.length,
  );
  const databaseMeasurements = latestDatabase?.result?.database_measurements || [];
  const databaseProfile = dashboard?.profiles.database[selectedDatabaseProfile];
  const maxDatabaseTps = Math.max(
    1,
    ...databaseMeasurements.map((item) => item.metrics.transactions_per_second || 0),
  );
  const activeWeb = dashboard?.runs.find(
    (run) => run.suite === "web" && ["queued", "running"].includes(run.status),
  );
  const latestWeb = dashboard?.runs.find(
    (run) => run.suite === "web" && run.status === "completed" && run.result?.web_measurements?.length,
  );
  const webMeasurements = latestWeb?.result?.web_measurements || [];
  const webProfile = dashboard?.profiles.web[selectedWebProfile];
  const maxWebRps = Math.max(
    1,
    ...webMeasurements.map((item) => item.metrics.requests_per_second || 0),
  );
  const latestStorage = dashboard?.runs.find(
    (run) => run.suite === "storage" && run.status === "completed" && run.result?.jobs?.length
      && (run.request?.agent_id || "local") === selectedExecutionTarget,
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

  function runTargetName(run: Run) {
    const remoteId = run.request?.agent_id;
    if (!remoteId) return "Controller host";
    return allAgents.find((agent) => agent.id === remoteId)?.name || remoteId;
  }

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
    if (!executionTargetOnline) {
      setNotice("The selected Agent is offline. Start its persistent worker before dispatching a benchmark.");
      return;
    }
    if (!executionInventory?.capabilities?.fio) {
      setNotice(`fio is not installed on ${executionTargetLabel}. Run CloudMark bootstrap --packs storage there first.`);
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      const response = await fetch(`${API}/runs`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CloudMark-Token": token },
        body: JSON.stringify({
          suite: "storage",
          profile: selectedStorageProfile,
          confirm_write: true,
          ...(selectedExecutionAgent ? { agent_id: selectedExecutionAgent.id } : {}),
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Unable to start the benchmark");
      setNotice(`Created ${payload.id} on ${executionTargetLabel} with the ${selectedProfile?.label || selectedStorageProfile} profile. The temporary file will be removed after the run.`);
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
        body: JSON.stringify({ label: "Provider paired assessment" }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Unable to create a pairing session");
      setPairing(payload);
      setSelectedSessionId(payload.id);
      setNotice("A 30-minute pairing session is ready for two provider Agents.");
      await loadDashboard();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to create the pairing session");
    } finally {
      setBusy(false);
    }
  }

  async function startSystemBenchmark(suite: "compute" | "memory") {
    if (!requireToken()) return;
    if (!executionTargetOnline) {
      setNotice("The selected Agent is offline. Start its persistent worker before dispatching a benchmark.");
      return;
    }
    if (suite === "memory" && executionInventory?.os?.system !== "Linux") {
      setNotice(`The native memory executor currently requires Linux with GCC and OpenMP; ${executionTargetLabel} is not eligible.`);
      return;
    }
    const capability = suite === "compute" ? "sysbench" : "gcc";
    if (!executionInventory?.capabilities?.[capability]) {
      setNotice(`${capability} is not installed on ${executionTargetLabel}. Run CloudMark bootstrap --packs ${suite} there first.`);
      return;
    }
    const profile = suite === "compute" ? selectedComputeProfile : selectedMemoryProfile;
    setBusy(true);
    setNotice(null);
    try {
      const response = await fetch(`${API}/runs`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CloudMark-Token": token },
        body: JSON.stringify({
          suite,
          profile,
          confirm_load: true,
          ...(selectedExecutionAgent ? { agent_id: selectedExecutionAgent.id } : {}),
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `Unable to start the ${suite} assessment`);
      setNotice(`Created ${payload.id} on ${executionTargetLabel}. Avoid other workloads until the intentional saturation run completes.`);
      await loadDashboard();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : `${suite} assessment failed`);
    } finally {
      setBusy(false);
    }
  }

  async function cancelSystemBenchmark() {
    if (!activeSystem || !requireToken()) return;
    setBusy(true);
    try {
      const response = await fetch(`${API}/runs/${activeSystem.id}/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CloudMark-Token": token },
        body: "{}",
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Unable to cancel the assessment");
      setNotice(`Cancellation requested for ${activeSystem.id}.`);
      await loadDashboard();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to cancel the assessment");
    } finally {
      setBusy(false);
    }
  }

  async function startNetwork() {
    if (!requireToken()) return;
    if (!selectedSession || selectedSession.status !== "ready") {
      setNotice("Join one target and one generator agent before starting a network run.");
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      const response = await fetch(`${API}/runs`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CloudMark-Token": token },
        body: JSON.stringify({
          suite: "network",
          profile: selectedNetworkProfile,
          session_id: selectedSession.id,
          confirm_network_load: true,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Unable to start the network assessment");
      setNotice(`Created ${payload.id}. Guarded network traffic will flow only between the paired agents.`);
      await loadDashboard();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Network assessment failed");
    } finally {
      setBusy(false);
    }
  }

  async function cancelNetwork() {
    if (!activeNetwork || !requireToken()) return;
    setBusy(true);
    try {
      const response = await fetch(`${API}/runs/${activeNetwork.id}/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CloudMark-Token": token },
        body: "{}",
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Unable to cancel the network assessment");
      setNotice(`Cancellation requested for ${activeNetwork.id}. Agent safety deadlines remain active.`);
      await loadDashboard();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to cancel the network assessment");
    } finally {
      setBusy(false);
    }
  }

  async function startDatabase() {
    if (!requireToken()) return;
    if (!selectedSession || selectedSession.status !== "ready") {
      setNotice("Join one target and one generator Agent before starting a database run.");
      return;
    }
    const target = selectedSession.agents.find((agent) => agent.role === "target");
    const generator = selectedSession.agents.find((agent) => agent.role === "generator");
    const targetCapabilities = target?.system.inventory?.capabilities || {};
    if (!targetCapabilities.postgres || !targetCapabilities.initdb || !targetCapabilities.pgbench || !targetCapabilities.pg_isready) {
      setNotice("The target Agent needs PostgreSQL server tools and pgbench. Install the CloudMark database pack and restart the Agent.");
      return;
    }
    if (!generator?.system.inventory?.capabilities?.pgbench) {
      setNotice("The generator Agent needs pgbench. Install the CloudMark database pack and restart the Agent.");
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      const response = await fetch(`${API}/runs`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CloudMark-Token": token },
        body: JSON.stringify({
          suite: "database",
          profile: selectedDatabaseProfile,
          session_id: selectedSession.id,
          confirm_database_load: true,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Unable to start the database assessment");
      setNotice(`Created ${payload.id}. PostgreSQL data and transaction traffic remain inside the paired provider Agents.`);
      await loadDashboard();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Database assessment failed");
    } finally {
      setBusy(false);
    }
  }

  async function cancelDatabase() {
    if (!activeDatabase || !requireToken()) return;
    setBusy(true);
    try {
      const response = await fetch(`${API}/runs/${activeDatabase.id}/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CloudMark-Token": token },
        body: "{}",
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Unable to cancel the database assessment");
      setNotice(`Cancellation requested for ${activeDatabase.id}. Ephemeral database cleanup remains mandatory.`);
      await loadDashboard();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to cancel the database assessment");
    } finally {
      setBusy(false);
    }
  }

  async function startWeb() {
    if (!requireToken()) return;
    if (!selectedSession || selectedSession.status !== "ready") {
      setNotice("Join one target and one generator Agent before starting a Web/API/TLS run.");
      return;
    }
    const target = selectedSession.agents.find((agent) => agent.role === "target");
    const generator = selectedSession.agents.find((agent) => agent.role === "generator");
    const targetCapabilities = target?.system.inventory?.capabilities || {};
    if (!targetCapabilities.nginx || !targetCapabilities.openssl) {
      setNotice("The target Agent needs Nginx and OpenSSL. Install the CloudMark web pack and restart the Agent.");
      return;
    }
    if (!generator?.system.inventory?.capabilities?.ab) {
      setNotice("The generator Agent needs ApacheBench. Install the CloudMark web pack and restart the Agent.");
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      const response = await fetch(`${API}/runs`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CloudMark-Token": token },
        body: JSON.stringify({
          suite: "web",
          profile: selectedWebProfile,
          session_id: selectedSession.id,
          confirm_web_load: true,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Unable to start the Web/API/TLS assessment");
      setNotice(`Created ${payload.id}. HTTP and TLS traffic will remain between the paired provider Agents.`);
      await loadDashboard();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Web/API/TLS assessment failed");
    } finally {
      setBusy(false);
    }
  }

  async function cancelWeb() {
    if (!activeWeb || !requireToken()) return;
    setBusy(true);
    try {
      const response = await fetch(`${API}/runs/${activeWeb.id}/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CloudMark-Token": token },
        body: "{}",
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Unable to cancel the Web/API/TLS assessment");
      setNotice(`Cancellation requested for ${activeWeb.id}. Ephemeral Nginx cleanup remains mandatory.`);
      await loadDashboard();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unable to cancel the Web/API/TLS assessment");
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
    ["compute", "Compute & Memory", "03"],
    ["storage", "Storage Assessment", "04"],
    ["network", "Distributed Testing", "05"],
    ["database", "Database Assessment", "06"],
    ["web", "Web & API Assessment", "07"],
    ["scenarios", "Workload Suitability", "08"],
    ["history", "History", "09"],
  ];

  const executionTargetPanel = (
    <section className="panel execution-target-panel">
      <div>
        <span className="section-kicker">EXECUTION TARGET</span>
        <h3>{executionTargetLabel}</h3>
        <p>{selectedExecutionAgent ? `${selectedExecutionAgent.system.provider?.provider || "Unverified provider"} · ${selectedExecutionAgent.system.inventory?.os?.distribution || selectedExecutionAgent.system.inventory?.os?.system || "Unknown OS"}` : "Benchmarks execute on the same host as the local Controller."}</p>
      </div>
      <label>
        <span>HOST</span>
        <select value={selectedExecutionTarget} onChange={(event) => setSelectedExecutionTarget(event.target.value)}>
          <option value="local">Controller host · local</option>
          {allAgents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name} · {agent.role} · {agent.status}</option>)}
        </select>
      </label>
      <div className="target-capabilities">
        <span className={executionTargetOnline ? "ready" : "missing"}>{executionTargetOnline ? "ONLINE" : "OFFLINE"}</span>
        <span className={executionInventory?.capabilities?.sysbench ? "ready" : "missing"}>CPU</span>
        <span className={memoryReady ? "ready" : "missing"}>MEMORY</span>
        <span className={executionInventory?.capabilities?.fio ? "ready" : "missing"}>STORAGE</span>
      </div>
    </section>
  );

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
        <div className="version">CORE / {dashboard?.version || "0.5.0"}</div>
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

        {activeView === "compute" && (
          <div className="view compute-view">
            <section className="section-intro">
              <div><span className="section-kicker">LOCAL SATURATION EXECUTORS</span><h2>Separate single-core speed, scaling, sustained compute, and memory bandwidth.</h2><p>CPU profiles use a versioned sysbench workload with warm-up and one-second stability evidence. Memory profiles compile CloudMark&apos;s cache-resistant OpenMP kernels and retain the compiler version, working set, host utilization, and steal time.</p></div>
              <div className="load-policy"><span>EXCLUSIVE LOAD POLICY</span><strong>{activeLocal ? `${activeLocal.suite} run active` : "Selected target available"}</strong><small>Compute, memory, and storage never overlap on the same target.</small></div>
            </section>
            {executionTargetPanel}
            <section className="system-runner-grid">
              <article className="panel system-runner-card compute-card">
                <div className="panel-head"><div><span className="section-kicker">CPU / INTEGER SCALING</span><h3>Compute assessment</h3></div><span className={executionInventory?.capabilities?.sysbench ? "tool-ready" : "tool-missing"}>{executionInventory?.capabilities?.sysbench ? "SYSBENCH READY" : "INSTALL SYSBENCH"}</span></div>
                <p>{dashboard?.profiles.compute[selectedComputeProfile]?.description}</p>
                <label><span>PROFILE</span><select value={selectedComputeProfile} onChange={(event) => setSelectedComputeProfile(event.target.value)} disabled={Boolean(activeLocal)}>{Object.entries(dashboard?.profiles.compute || {}).map(([id, profile]) => <option key={id} value={id}>{profile.label} · ≈ {profile.estimated_minutes} min</option>)}</select></label>
                <div className="runner-matrix">{dashboard?.profiles.compute[selectedComputeProfile]?.jobs.map((job) => <span key={job.name}>{job.name}</span>)}</div>
                <button className="button primary" onClick={() => startSystemBenchmark("compute")} disabled={busy || Boolean(activeLocal)}>Run compute profile</button>
              </article>
              <article className="panel system-runner-card memory-card">
                <div className="panel-head"><div><span className="section-kicker">RAM / BANDWIDTH</span><h3>Memory assessment</h3></div><span className={memoryReady ? "tool-ready" : "tool-missing"}>{memoryReady ? "LINUX + GCC READY" : executionInventory?.os?.system === "Linux" ? "INSTALL GCC" : "LINUX REQUIRED"}</span></div>
                <p>{dashboard?.profiles.memory[selectedMemoryProfile]?.description}</p>
                <label><span>PROFILE</span><select value={selectedMemoryProfile} onChange={(event) => setSelectedMemoryProfile(event.target.value)} disabled={Boolean(activeLocal)}>{Object.entries(dashboard?.profiles.memory || {}).map(([id, profile]) => <option key={id} value={id}>{profile.label} · ≈ {profile.estimated_minutes} min</option>)}</select></label>
                <div className="runner-matrix">{dashboard?.profiles.memory[selectedMemoryProfile]?.jobs.map((job) => <span key={job.name}>{job.name}</span>)}</div>
                <button className="button primary" onClick={() => startSystemBenchmark("memory")} disabled={busy || Boolean(activeLocal) || !memoryReady}>Run memory profile</button>
              </article>
            </section>
            {activeSystem && <section className="panel run-progress" aria-live="polite"><div><span className="section-kicker">ACTIVE {activeSystem.suite.toUpperCase()} RUN / {activeSystem.id}</span><strong>{activeSystem.current_job || activeSystem.phase || "Starting"}</strong><small>{activeSystem.completed_steps || 0} of {activeSystem.total_steps || 1} jobs · {Math.round((activeSystem.progress || 0) * 100)}%</small></div><div className="progress-track"><i style={{ width: `${Math.max(2, (activeSystem.progress || 0) * 100)}%` }} /></div><button className="button danger" onClick={cancelSystemBenchmark} disabled={busy || activeSystem.cancel_requested}>{activeSystem.cancel_requested ? "Cancelling" : "Cancel run"}</button></section>}
            <section className="system-results-grid">
              <article className="panel system-result-panel">
                <div className="panel-head"><div><span className="section-kicker">LATEST COMPUTE EVIDENCE</span><h3>Integer events per second</h3></div><span className="run-id">{latestCompute?.id || "NO RUN YET"}</span></div>
                {computeJobs.length ? <div className="bar-chart">{computeJobs.map((job) => <div className="bar-row" key={job.name}><span>{job.name} · T{job.threads}</span><div><i style={{ width: `${Math.max(3, (job.metrics.events_per_second / maxComputeRate) * 100)}%` }} /></div><strong>{Math.round(job.metrics.events_per_second).toLocaleString()} eps</strong></div>)}</div> : <div className="empty-chart compact"><div className="chart-grid" /><strong>No compute result yet</strong><p>Run Compute Quick on an otherwise idle machine to establish the first versioned baseline.</p></div>}
                {latestCompute?.result?.scaling && <div className="result-summary"><div><span>ALL-CORE SCALE</span><strong>{latestCompute.result.scaling.all_core_threads}× threads</strong></div><div><span>SCALING EFFICIENCY</span><strong>{latestCompute.result.scaling.efficiency_percent == null ? "—" : `${latestCompute.result.scaling.efficiency_percent.toFixed(1)}%`}</strong></div></div>}
              </article>
              <article className="panel system-result-panel">
                <div className="panel-head"><div><span className="section-kicker">LATEST MEMORY EVIDENCE</span><h3>Effective kernel bandwidth</h3></div><span className="run-id">{latestMemory?.id || "NO RUN YET"}</span></div>
                {memoryJobs.length ? <div className="bar-chart">{memoryJobs.map((job) => <div className="bar-row" key={job.name}><span>{job.name} · T{job.threads}</span><div><i style={{ width: `${Math.max(3, (job.metrics.bandwidth_bytes_per_second / maxMemoryRate) * 100)}%` }} /></div><strong>{formatBytes(job.metrics.bandwidth_bytes_per_second)}/s</strong></div>)}</div> : <div className="empty-chart compact"><div className="chart-grid" /><strong>No memory result yet</strong><p>Memory Quick allocates three fixed arrays and preserves GCC and native benchmark versions.</p></div>}
              </article>
            </section>
            <section className="validity-panel panel"><span>COMPARISON CONTRACT</span><p>Compare only identical CloudMark profile, methodology, tool/compiler version, architecture, thread count, and controlled background-load conditions. CPU events and native memory bandwidth are evidence dimensions—not a universal machine score.</p></section>
          </div>
        )}

        {activeView === "storage" && (
          <div className="view storage-view">
            <section className="section-intro">
              <div><span className="section-kicker">CURRENT AVAILABLE EXECUTOR</span><h2>Measure storage by workload, not by a single MB/s number.</h2><p>Five profiles cover short validation, general-purpose, database, large-block throughput, and sustained behavior using safe 512 MiB–8 GiB temporary files.</p></div>
              <div className="runner-actions">
                <label><span>PROFILE</span><select value={selectedStorageProfile} onChange={(event) => setSelectedStorageProfile(event.target.value)} disabled={Boolean(activeLocal)}>{Object.entries(dashboard?.profiles.storage || {}).map(([id, profile]) => <option key={id} value={id}>{profile.label} · ≈ {profile.estimated_minutes} min</option>)}</select></label>
                <button className="button primary" onClick={startStorage} disabled={busy || Boolean(activeLocal)}>Run assessment</button>
              </div>
            </section>
            {executionTargetPanel}
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
              ) : <div className="timeline-empty">Run any storage profile to capture one-second bandwidth, IOPS, and latency evidence.</div>}
            </section>
          </div>
        )}

        {activeView === "network" && (
          <div className="view network-view">
            <section className="section-intro"><div><span className="section-kicker">DISTRIBUTED ASSESSMENT</span><h2>Keep the control path separate from benchmark data traffic.</h2><p>The Controller schedules guarded tasks and stores evidence. TCP, adaptive-rate UDP, and ICMP measurements flow directly between the two provider Agents.</p></div><div className="runner-actions"><label><span>PROFILE</span><select value={selectedNetworkProfile} onChange={(event) => setSelectedNetworkProfile(event.target.value)} disabled={Boolean(activeNetwork)}>{Object.entries(dashboard?.profiles.network || {}).map(([id, profile]) => <option key={id} value={id}>{profile.label}</option>)}</select></label><button className="button primary" onClick={createPairing} disabled={busy}>New session</button></div></section>
            <section className="topology-panel panel">
              <div className="topology-node controller"><span>LOCAL</span><strong>Controller</strong><small>Dashboard + API</small></div>
              <div className="control-line"><span>HTTPS / VPN control</span></div>
              <div className="cloud-boundary">
                <span className="boundary-label">PROVIDER NETWORK</span>
                <div className="topology-node target"><span>VM A</span><strong>Target</strong><small>web · db · storage</small></div>
                <div className="data-line"><i /><span>A ↔ B direct guarded traffic</span></div>
                <div className="topology-node generator"><span>VM B</span><strong>Generator</strong><small>iperf3 · guarded load</small></div>
              </div>
              <div className="blocked-line"><span>×</span><p><strong>Cloud → controller measurement</strong><small>Disabled by project policy</small></p></div>
            </section>
            {pairing && <section className="pairing-card"><div><span>SHORT-LIVED JOIN CREDENTIAL</span><strong>{pairing.id}</strong><small>Expires {new Date(pairing.expires_at).toLocaleTimeString("en-US")}</small></div><code>{pairing.join_token}</code></section>}
            {pairing && <section className="panel agent-commands"><div><span className="section-kicker">RUN ON PROVIDER VMS</span><h3>Start one persistent worker on each clean machine</h3></div><code>cloudmark agent --controller https://CONTROLLER --session {pairing.id} --token {pairing.join_token} --role target --advertise-address VM_A_IP</code><code>cloudmark agent --controller https://CONTROLLER --session {pairing.id} --token {pairing.join_token} --role generator --advertise-address VM_B_IP</code><p>Use <strong>--allow-http</strong> only when Controller access is restricted to a trusted private management network.</p></section>}
            <section className="panel session-panel">
              <div className="panel-head"><div><span className="section-kicker">AGENT CONTROL PLANE</span><h3>Pairing readiness</h3></div><label className="compact-select"><span>SESSION</span><select value={selectedSession?.id || ""} onChange={(event) => setSelectedSessionId(event.target.value)}>{dashboard?.sessions.map((session) => <option key={session.id} value={session.id}>{session.label} · {session.status}</option>)}</select></label></div>
              {selectedSession ? <div className="agent-roster">{["target", "generator"].map((role) => { const agent = selectedSession.agents.find((item) => item.role === role); return <article key={role} className={agent ? "connected" : "waiting"}><span>{role.toUpperCase()}</span><strong>{agent?.name || `Waiting for ${role}`}</strong><small>{agent ? `${agent.endpoint.address || "No advertised IP"} · ${agent.system.inventory?.capabilities?.iperf3 ? "iperf3 ready" : "iperf3 missing"}` : "Join command has not connected"}</small></article>; })}</div> : <div className="empty-row">Create a session, then connect both provider agents.</div>}
              <div className="session-actions"><p><strong>{selectedSession?.status === "ready" ? "Ready to measure" : "Two agents required"}</strong><small>Only allow-listed iperf3 ports, capped UDP rates, and bounded peer ping tasks can be dispatched.</small></p><button className="button primary" onClick={startNetwork} disabled={busy || Boolean(activeNetwork) || selectedSession?.status !== "ready"}>Run network assessment</button></div>
            </section>
            {activeNetwork && <section className="panel run-progress" aria-live="polite"><div><span className="section-kicker">ACTIVE NETWORK RUN / {activeNetwork.id}</span><strong>{activeNetwork.current_job || activeNetwork.phase || "Waiting for agents"}</strong><small>{activeNetwork.completed_steps || 0} of {activeNetwork.total_steps || 1} measurements · {Math.round((activeNetwork.progress || 0) * 100)}%</small></div><div className="progress-track"><i style={{ width: `${Math.max(2, (activeNetwork.progress || 0) * 100)}%` }} /></div><button className="button danger" onClick={cancelNetwork} disabled={busy || activeNetwork.cancel_requested}>{activeNetwork.cancel_requested ? "Cancelling" : "Cancel run"}</button></section>}
            <section className="panel network-results">
              <div className="panel-head"><div><span className="section-kicker">LATEST VERIFIED RUN</span><h3>Peer TCP throughput</h3></div><span className="run-id">{latestNetwork?.id || "NO RUN YET"}</span></div>
              {networkMeasurements.length ? <div className="bar-chart">{networkMeasurements.map((measurement, index) => { const rate = measurement.metrics.received_bits_per_second || 0; return <div className="bar-row" key={`${measurement.direction}-${measurement.streams}-${index}`}><span>{measurement.sender.name} → {measurement.receiver.name} · P{measurement.streams}</span><div><i style={{ width: `${Math.max(3, (rate / maxNetworkRate) * 100)}%` }} /></div><strong>{(rate / 1_000_000).toFixed(1)} Mb/s</strong></div>; })}</div> : <div className="empty-chart"><div className="chart-grid" /><strong>No peer result yet</strong><p>Connect two agents and run a profile to establish a directional baseline.</p></div>}
            </section>
            {(latencyMeasurements.length > 0 || udpMeasurements.length > 0 || bidirectionalMeasurements.length > 0) && (
              <section className="network-evidence-grid">
                <article className="panel network-evidence-card">
                  <div className="panel-head"><div><span className="section-kicker">LATENCY UNDER LOAD</span><h3>Idle versus TCP RTT</h3></div><span className="run-id">UNSCORED</span></div>
                  <div className="evidence-rows">{networkAnalysis.map((item) => <div key={item.direction}><span>{item.direction}</span><strong>{item.idle_icmp_average_ms?.toFixed(2) ?? "—"} ms idle</strong><small>{item.loaded_tcp_rtt_mean_ms?.toFixed(2) ?? "—"} ms loaded · {item.latency_inflation_percent?.toFixed(1) ?? "—"}% change</small></div>)}</div>
                  <p className="method-note">ICMP idle latency and TCP_INFO RTT use different protocols, so CloudMark records the comparison without assigning a bufferbloat score.</p>
                </article>
                <article className="panel network-evidence-card">
                  <div className="panel-head"><div><span className="section-kicker">ADAPTIVE UDP</span><h3>Loss and jitter sweep</h3></div><span className="run-id">{udpMeasurements.length} SAMPLES</span></div>
                  <div className="evidence-rows">{udpMeasurements.map((item, index) => <div key={`${item.direction}-${item.target_rate_bps}-${index}`}><span>{item.sender.name} → {item.receiver.name} · {Math.round(item.rate_fraction_of_tcp_peak * 100)}%</span><strong>{((item.metrics.received_bits_per_second || 0) / 1_000_000).toFixed(1)} Mb/s</strong><small>{item.metrics.lost_percent?.toFixed(2) ?? "—"}% loss · {item.metrics.jitter_ms?.toFixed(3) ?? "—"} ms jitter</small></div>)}</div>
                </article>
                <article className="panel network-evidence-card bidirectional-card">
                  <div className="panel-head"><div><span className="section-kicker">SIMULTANEOUS BIDIRECTIONAL</span><h3>Duplex pressure</h3></div><span className="run-id">TCP</span></div>
                  <div className="evidence-rows">{bidirectionalMeasurements.map((item, index) => <div key={`${item.direction}-${index}`}><span>{item.sender.name} ↔ {item.receiver.name} · P{item.streams}</span><strong>{((item.metrics.forward.received_bits_per_second || 0) / 1_000_000).toFixed(1)} / {((item.metrics.reverse.received_bits_per_second || 0) / 1_000_000).toFixed(1)} Mb/s</strong><small>forward / reverse receiver throughput</small></div>)}</div>
                </article>
              </section>
            )}
            <section className="network-checks">
              {[["TCP", "1 / 4 / 8 / 16 streams", "AVAILABLE"], ["UDP", "25 / 50 / 90% adaptive sweep", "AVAILABLE IN STANDARD"], ["LATENCY", "idle ICMP · loaded TCP RTT", "AVAILABLE IN STANDARD"], ["DIRECTION", "A→B · B→A · simultaneous", "AVAILABLE"]].map(([name, detail, state]) => <article key={name}><span>{name}</span><strong>{detail}</strong><small>{state}</small></article>)}
            </section>
          </div>
        )}

        {activeView === "database" && (
          <div className="view database-view">
            <section className="section-intro">
              <div><span className="section-kicker">TWO-AGENT DATABASE ASSESSMENT</span><h2>Measure the database service, storage path, CPU, and provider network together.</h2><p>CloudMark creates an isolated PostgreSQL cluster on the Target and dispatches exact pgbench workloads from the Generator. The Controller stores evidence but never carries transaction traffic.</p></div>
              <div className="runner-actions"><label><span>PROFILE</span><select value={selectedDatabaseProfile} onChange={(event) => setSelectedDatabaseProfile(event.target.value)} disabled={Boolean(activeDatabase)}>{Object.entries(dashboard?.profiles.database || {}).map(([id, profile]) => <option key={id} value={id}>{profile.label} · ≈ {profile.estimated_minutes} min</option>)}</select></label><button className="button primary" onClick={createPairing} disabled={busy}>New session</button></div>
            </section>
            <section className="database-contract-grid">
              <article className="panel database-profile-card">
                <div className="panel-head"><div><span className="section-kicker">VERSIONED WORKLOAD</span><h3>{databaseProfile?.label || "PostgreSQL profile"}</h3></div><span className="run-id">SCALE {databaseProfile?.scale_factor || "—"}</span></div>
                <p>{databaseProfile?.description}</p>
                <div className="database-job-grid">{databaseProfile?.jobs.map((job) => <div key={job.name}><span>{job.workload}</span><strong>{job.name}</strong><small>C{job.clients} · J{job.threads} · {job.duration}s{job.connect_per_transaction ? " · reconnect" : ""}</small></div>)}</div>
              </article>
              <article className="panel database-safety-card">
                <span className="section-kicker">EXECUTION CONTRACT</span><h3>Ephemeral and bounded by design</h3>
                <ul><li>Target-only temporary PostgreSQL cluster</li><li>Generator address is the only remote database client</li><li>Durability remains enabled: fsync, full-page writes, synchronous commit</li><li>Fixed port 55432 and allow-listed built-in pgbench scripts</li><li>Dataset and logs removed after success, failure, timeout, or cancellation</li></ul>
                <p>Transaction tail percentiles are not claimed in PostgreSQL v1; average latency, failures, TPS, and one-second progress are retained.</p>
              </article>
            </section>
            {pairing && <section className="pairing-card"><div><span>SHORT-LIVED JOIN CREDENTIAL</span><strong>{pairing.id}</strong><small>Expires {new Date(pairing.expires_at).toLocaleTimeString("en-US")}</small></div><code>{pairing.join_token}</code></section>}
            {pairing && <section className="panel agent-commands"><div><span className="section-kicker">RUN ON PROVIDER VMS</span><h3>Target hosts PostgreSQL; Generator runs pgbench</h3></div><code>cloudmark agent --controller https://CONTROLLER --session {pairing.id} --token {pairing.join_token} --role target --advertise-address VM_A_IP</code><code>cloudmark agent --controller https://CONTROLLER --session {pairing.id} --token {pairing.join_token} --role generator --advertise-address VM_B_IP</code><p>Install the <strong>database</strong> pack on both machines before starting their Agents.</p></section>}
            <section className="panel session-panel">
              <div className="panel-head"><div><span className="section-kicker">PAIRED EXECUTION</span><h3>Database readiness</h3></div><label className="compact-select"><span>SESSION</span><select value={selectedSession?.id || ""} onChange={(event) => setSelectedSessionId(event.target.value)}>{dashboard?.sessions.map((session) => <option key={session.id} value={session.id}>{session.label} · {session.status}</option>)}</select></label></div>
              {selectedSession ? <div className="agent-roster">{["target", "generator"].map((role) => { const agent = selectedSession.agents.find((item) => item.role === role); const ready = role === "target" ? Boolean(agent?.system.inventory?.capabilities?.postgres && agent?.system.inventory?.capabilities?.initdb && agent?.system.inventory?.capabilities?.pgbench && agent?.system.inventory?.capabilities?.pg_isready) : Boolean(agent?.system.inventory?.capabilities?.pgbench); return <article key={role} className={agent && ready ? "connected" : "waiting"}><span>{role.toUpperCase()}</span><strong>{agent?.name || `Waiting for ${role}`}</strong><small>{agent ? `${agent.endpoint.address || "No advertised IP"} · ${ready ? "database tools ready" : "database pack required"}` : "Join command has not connected"}</small></article>; })}</div> : <div className="empty-row">Create a session, then connect both provider Agents.</div>}
              <div className="session-actions"><p><strong>{selectedSession?.status === "ready" ? "Pair connected" : "Two Agents required"}</strong><small>The Controller validates PostgreSQL capabilities again before accepting the run.</small></p><button className="button primary" onClick={startDatabase} disabled={busy || Boolean(activeDatabase) || selectedSession?.status !== "ready"}>Run database assessment</button></div>
            </section>
            {activeDatabase && <section className="panel run-progress" aria-live="polite"><div><span className="section-kicker">ACTIVE DATABASE RUN / {activeDatabase.id}</span><strong>{activeDatabase.current_job || activeDatabase.phase || "Preparing PostgreSQL"}</strong><small>{activeDatabase.completed_steps || 0} of {activeDatabase.total_steps || 1} steps · {Math.round((activeDatabase.progress || 0) * 100)}%</small></div><div className="progress-track"><i style={{ width: `${Math.max(2, (activeDatabase.progress || 0) * 100)}%` }} /></div><button className="button danger" onClick={cancelDatabase} disabled={busy || activeDatabase.cancel_requested}>{activeDatabase.cancel_requested ? "Cancelling" : "Cancel run"}</button></section>}
            <section className="panel database-results">
              <div className="panel-head"><div><span className="section-kicker">LATEST COMPLETED RUN</span><h3>PostgreSQL transaction throughput</h3></div><span className="run-id">{latestDatabase?.id || "NO RUN YET"}</span></div>
              {databaseMeasurements.length ? <div className="bar-chart">{databaseMeasurements.map((measurement) => { const tps = measurement.metrics.transactions_per_second || 0; return <div className="bar-row" key={measurement.name}><span>{measurement.name} · C{measurement.clients}</span><div><i style={{ width: `${Math.max(3, (tps / maxDatabaseTps) * 100)}%` }} /></div><strong>{Math.round(tps).toLocaleString()} TPS</strong></div>; })}</div> : <div className="empty-chart compact"><div className="chart-grid" /><strong>No PostgreSQL result yet</strong><p>Connect a prepared target and generator to establish the first database baseline.</p></div>}
            </section>
            {databaseMeasurements.length > 0 && <section className="database-evidence-grid">
              {databaseMeasurements.map((measurement) => <article className="panel" key={measurement.name}><span>{measurement.workload.toUpperCase()} · C{measurement.clients} / J{measurement.threads}</span><strong>{measurement.metrics.latency_average_ms.toFixed(2)} ms</strong><small>{measurement.metrics.failed_transactions} failed · {measurement.metrics.transactions_processed.toLocaleString()} transactions</small></article>)}
              <article className={`panel cleanup-evidence ${latestDatabase?.result?.cleanup?.cleanup_verified ? "verified" : "unknown"}`}><span>EPHEMERAL CLEANUP</span><strong>{latestDatabase?.result?.cleanup?.cleanup_verified ? "Verified" : "Unavailable"}</strong><small>{latestDatabase?.result?.server?.estimated_dataset_bytes ? `${formatBytes(latestDatabase.result.server.estimated_dataset_bytes)} estimated dataset` : "Dataset size unavailable"}</small></article>
            </section>}
          </div>
        )}

        {activeView === "web" && (
          <div className="view web-view">
            <section className="section-intro">
              <div><span className="section-kicker">TWO-AGENT WEB, API & TLS ASSESSMENT</span><h2>Measure serving capacity, tail latency, TLS cost, and static transfer without using the Controller as a traffic endpoint.</h2><p>CloudMark starts an isolated Nginx service on the Target and runs bounded ApacheBench jobs from the Generator. Only fixed CloudMark endpoints, ports, and concurrency levels are accepted.</p></div>
              <div className="runner-actions"><label><span>PROFILE</span><select value={selectedWebProfile} onChange={(event) => setSelectedWebProfile(event.target.value)} disabled={Boolean(activeWeb)}>{Object.entries(dashboard?.profiles.web || {}).map(([id, profile]) => <option key={id} value={id}>{profile.label} · ≈ {profile.estimated_minutes} min</option>)}</select></label><button className="button primary" onClick={createPairing} disabled={busy}>New session</button></div>
            </section>
            <section className="web-contract-grid">
              <article className="panel web-profile-card">
                <div className="panel-head"><div><span className="section-kicker">VERSIONED WORKLOAD</span><h3>{webProfile?.label || "Web & TLS profile"}</h3></div><span className="run-id">HTTP {webProfile?.http_port || "—"} / TLS {webProfile?.https_port || "—"}</span></div>
                <p>{webProfile?.description}</p>
                <div className="web-job-grid">{webProfile?.jobs.map((job) => <div key={job.name}><span>{job.scheme.toUpperCase()}</span><strong>{job.name}</strong><small>C{job.concurrency} · {job.path} · {job.duration}s · {job.keep_alive ? "keep-alive" : "new connections"}</small></div>)}</div>
              </article>
              <article className="panel web-safety-card">
                <span className="section-kicker">EXECUTION CONTRACT</span><h3>Owned, isolated, and bounded</h3>
                <ul><li>Exact Target address; never binds to all interfaces</li><li>Only the paired Generator and Target addresses are allowed</li><li>Fixed health, 1 KiB JSON, and 256 KiB static payloads</li><li>Ephemeral self-signed certificate with a fixed TLS 1.2 methodology</li><li>Temporary service files and keys are removed after every terminal path</li></ul>
                <p>This is controlled load testing, not DDoS testing. Arbitrary URLs, ports, payloads, and external targets are rejected.</p>
              </article>
            </section>
            {pairing && <section className="pairing-card"><div><span>SHORT-LIVED JOIN CREDENTIAL</span><strong>{pairing.id}</strong><small>Expires {new Date(pairing.expires_at).toLocaleTimeString("en-US")}</small></div><code>{pairing.join_token}</code></section>}
            {pairing && <section className="panel agent-commands"><div><span className="section-kicker">RUN ON PROVIDER VMS</span><h3>Target hosts Nginx; Generator runs ApacheBench</h3></div><code>cloudmark agent --controller https://CONTROLLER --session {pairing.id} --token {pairing.join_token} --role target --advertise-address VM_A_IP</code><code>cloudmark agent --controller https://CONTROLLER --session {pairing.id} --token {pairing.join_token} --role generator --advertise-address VM_B_IP</code><p>Install the <strong>web</strong> pack on both machines and open TCP 58080 and 58443 only between the paired machines.</p></section>}
            <section className="panel session-panel">
              <div className="panel-head"><div><span className="section-kicker">PAIRED EXECUTION</span><h3>Web assessment readiness</h3></div><label className="compact-select"><span>SESSION</span><select value={selectedSession?.id || ""} onChange={(event) => setSelectedSessionId(event.target.value)}>{dashboard?.sessions.map((session) => <option key={session.id} value={session.id}>{session.label} · {session.status}</option>)}</select></label></div>
              {selectedSession ? <div className="agent-roster">{["target", "generator"].map((role) => { const agent = selectedSession.agents.find((item) => item.role === role); const ready = role === "target" ? Boolean(agent?.system.inventory?.capabilities?.nginx && agent?.system.inventory?.capabilities?.openssl) : Boolean(agent?.system.inventory?.capabilities?.ab); return <article key={role} className={agent && ready ? "connected" : "waiting"}><span>{role.toUpperCase()}</span><strong>{agent?.name || `Waiting for ${role}`}</strong><small>{agent ? `${agent.endpoint.address || "No advertised IP"} · ${ready ? "web tools ready" : "web pack required"}` : "Join command has not connected"}</small></article>; })}</div> : <div className="empty-row">Create a session, then connect both provider Agents.</div>}
              <div className="session-actions"><p><strong>{selectedSession?.status === "ready" ? "Pair connected" : "Two Agents required"}</strong><small>The Controller validates role-specific tools and the exact profile before accepting the run.</small></p><button className="button primary" onClick={startWeb} disabled={busy || Boolean(activeWeb) || selectedSession?.status !== "ready"}>Run Web/API/TLS assessment</button></div>
            </section>
            {activeWeb && <section className="panel run-progress" aria-live="polite"><div><span className="section-kicker">ACTIVE WEB RUN / {activeWeb.id}</span><strong>{activeWeb.current_job || activeWeb.phase || "Preparing isolated Nginx"}</strong><small>{activeWeb.completed_steps || 0} of {activeWeb.total_steps || 1} steps · {Math.round((activeWeb.progress || 0) * 100)}%</small></div><div className="progress-track"><i style={{ width: `${Math.max(2, (activeWeb.progress || 0) * 100)}%` }} /></div><button className="button danger" onClick={cancelWeb} disabled={busy || activeWeb.cancel_requested}>{activeWeb.cancel_requested ? "Cancelling" : "Cancel run"}</button></section>}
            <section className="panel web-results">
              <div className="panel-head"><div><span className="section-kicker">LATEST COMPLETED RUN</span><h3>HTTP request throughput by workload</h3></div><span className="run-id">{latestWeb?.id || "NO RUN YET"}</span></div>
              {webMeasurements.length ? <div className="bar-chart">{webMeasurements.map((measurement) => { const rps = measurement.metrics.requests_per_second || 0; return <div className="bar-row" key={measurement.name}><span>{measurement.name} · C{measurement.concurrency}</span><div><i style={{ width: `${Math.max(3, (rps / maxWebRps) * 100)}%` }} /></div><strong>{Math.round(rps).toLocaleString()} req/s</strong></div>; })}</div> : <div className="empty-chart compact"><div className="chart-grid" /><strong>No Web/API/TLS result yet</strong><p>Connect a prepared Target and Generator to establish the first controlled serving baseline.</p></div>}
            </section>
            {webMeasurements.length > 0 && <section className="web-evidence-grid">
              {webMeasurements.map((measurement) => <article className="panel" key={measurement.name}><span>{measurement.scheme.toUpperCase()} · C{measurement.concurrency} · {measurement.keep_alive ? "KEEP-ALIVE" : "NEW CONNECTION"}</span><strong>{measurement.metrics.latency_percentiles_ms.p95.toFixed(2)} ms p95</strong><small>{measurement.metrics.latency_percentiles_ms.p99.toFixed(2)} ms p99 · {measurement.metrics.success_percent.toFixed(3)}% success · {measurement.metrics.transfer_rate_kib_per_second?.toFixed(1) ?? "—"} KiB/s</small></article>)}
              <article className="panel"><span>TLS EVIDENCE</span><strong>{webMeasurements.find((measurement) => measurement.metrics.tls.status === "measured")?.metrics.tls.protocol || "Unavailable"}</strong><small>Ephemeral self-signed certificate · trust-chain issuance is not evaluated</small></article>
              <article className={`panel cleanup-evidence ${latestWeb?.result?.cleanup?.cleanup_verified ? "verified" : "unknown"}`}><span>EPHEMERAL CLEANUP</span><strong>{latestWeb?.result?.cleanup?.cleanup_verified ? "Verified" : "Unavailable"}</strong><small>Service directory, certificate, private key, payloads, and process state</small></article>
            </section>}
            <section className="panel web-method-note"><span className="section-kicker">INTERPRETATION LIMITS</span><p>ApacheBench can become the throughput bottleneck, so CloudMark retains this domain as Partial and does not assign provider suitability from these measurements alone. Dynamic application runtimes, reverse proxies, HTTP/2, HTTP/3, CDN, WAF, autoscaling, and DDoS resilience require separate evidence.</p></section>
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
                <div className="table-row" key={run.id}><code>{run.id}</code><span>{run.suite} / {run.profile}<small>{runTargetName(run)}</small></span><span className={`run-status ${run.status}`}>{statusLabel(run.status)}{run.status === "running" ? ` · ${Math.round((run.progress || 0) * 100)}%` : ""}</span><span>{run.started_at ? new Date(run.started_at).toLocaleString("en-US") : "—"}</span></div>
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
