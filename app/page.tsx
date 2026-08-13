"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

const API = "http://127.0.0.1:8787/api/v1";

type TopologyScope = "undeclared" | "same-host" | "same-zone" | "cross-zone" | "cross-region" | "public-internet";
type PairTopology = {
  scope: TopologyScope;
  source: "unavailable" | "operator-declared";
  verification: {
    status: "pending" | "unavailable" | "derived" | "confirmed" | "compatible" | "contradicted";
    observed_scope: TopologyScope | null;
    source: "unavailable" | "provider-metadata" | "advertised-endpoint-classification";
    reasons: string[];
  };
};

const TOPOLOGY_OPTIONS: { value: TopologyScope; label: string }[] = [
  { value: "undeclared", label: "Not declared" },
  { value: "same-host", label: "Same physical host" },
  { value: "same-zone", label: "Same zone" },
  { value: "cross-zone", label: "Cross-zone" },
  { value: "cross-region", label: "Cross-region" },
  { value: "public-internet", label: "Public Internet" },
];

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
    sender_cpu_percent?: number;
    receiver_cpu_percent?: number;
  };
};

type NetworkPathMeasurement = {
  direction: string;
  sender: NetworkEndpoint;
  receiver: NetworkEndpoint;
  evidence: {
    status: "complete" | "partial" | "unavailable";
    reason?: string;
    route?: {
      destination?: string;
      gateway?: string | null;
      gateway_address_class?: string | null;
      source?: string | null;
      interface?: string;
    };
    interface?: {
      name?: string;
      mtu_bytes?: number;
      state?: string;
      link_type?: string;
      driver?: {
        status: "observed" | "unavailable";
        driver?: string;
        version?: string;
        firmware_version?: string;
        bus_info?: string;
        reason?: string;
      };
      offloads?: {
        status: "observed" | "unavailable";
        features: Record<string, { enabled: boolean; fixed: boolean }>;
        reason?: string;
      };
      counters?: {
        status: "observed" | "partial" | "unavailable";
        rx_bytes?: number;
        rx_packets?: number;
        rx_errors?: number;
        rx_dropped?: number;
        tx_bytes?: number;
        tx_packets?: number;
        tx_errors?: number;
        tx_dropped?: number;
        source?: string;
        observed_at?: string;
        reason?: string;
      };
    };
    tcp?: {
      congestion_control?: {
        status: "observed" | "unavailable";
        algorithm?: string;
        source?: string;
        reason?: string;
      };
    };
    path_mtu?: { status: "observed" | "unavailable"; value_bytes?: number; source?: string };
    path_trace?: {
      status: "observed" | "partial" | "unavailable";
      tool?: string;
      max_hops: number;
      destination_address_class: string;
      hops: {
        hop: number;
        state: "observed" | "no-reply";
        address?: string | null;
        address_class?: string | null;
        rtt_ms?: number | null;
        reached_destination: boolean;
      }[];
      reached_destination: boolean;
      public_internet_traversal_proven: false;
      reason?: string;
      limitation?: string;
    };
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
    path_measurements?: NetworkPathMeasurement[];
    post_path_measurements?: NetworkPathMeasurement[];
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
        generator_headroom?: {
          status: "adequate" | "constrained" | "unknown";
          peak_cpu_percent?: number;
          stream_scaling_gain_percent?: number;
          reason_codes: string[];
        };
      }[];
      interface_counter_deltas?: {
        direction: string;
        sender?: NetworkEndpoint;
        receiver?: NetworkEndpoint;
        interface?: string;
        status: "observed" | "unavailable";
        counters?: {
          rx_bytes: number;
          rx_packets: number;
          rx_errors: number;
          rx_dropped: number;
          tx_bytes: number;
          tx_packets: number;
          tx_errors: number;
          tx_dropped: number;
        };
        total_errors?: number;
        total_dropped?: number;
        rx_drop_percent?: number;
        tx_drop_percent?: number;
        window_started_at?: string;
        window_ended_at?: string;
        reason?: string;
      }[];
      path_stability?: {
        direction: string;
        route_status: "observed" | "unavailable";
        route_stable?: boolean | null;
        trace_status: "observed" | "unavailable";
        trace_stable?: boolean | null;
        pre_observed_hops?: number;
        post_observed_hops?: number;
        route_reason?: string;
        trace_reason?: string;
        public_internet_traversal_proven: false;
      }[];
      path_claims?: {
        public_internet_traversal_proven: false;
        limitation: string;
      };
      validity?: {
        route_evidence_status: "complete" | "partial" | "unavailable";
        nic_evidence_status: "complete" | "partial" | "unavailable";
        nic_evidence_required: boolean;
        interface_counter_evidence_status: "complete" | "partial" | "unavailable";
        interface_counter_evidence_required: boolean;
        path_trace_evidence_status: "complete" | "partial" | "unavailable";
        path_trace_evidence_required: boolean;
        route_stability_status: "complete" | "partial" | "changed" | "unavailable";
        route_stability_required: boolean;
        generator_headroom_status: "adequate" | "constrained" | "unknown";
        comparison_eligible: boolean;
        reason_codes: string[];
      };
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
  topology: PairTopology;
  agents: Agent[];
};

type Scenario = { id: string; label: string; status: "available" | "partial" | "roadmap"; primary: string; coverage: string };
type AssessmentDomain = { id: string; label: string; status: "available" | "partial" | "roadmap"; summary: string };

type SuitabilityEvidence = {
  value: number;
  unit: string;
  source: string;
  run_id?: string;
  profile?: string;
  methodology_version?: string;
  observed_at?: string;
  quality: string;
  stale: boolean;
};

type SuitabilityCheck = {
  key: string;
  label: string;
  status: "pass" | "fail" | "unavailable" | "stale";
  operator: string;
  threshold: number;
  unit: string;
  evidence?: SuitabilityEvidence | null;
};

type SuitabilityScenario = {
  id: string;
  label: string;
  level: string;
  verdict: "insufficient" | "below-requirement" | "conditional-fit" | "suitable";
  coverage_percent: number;
  measured_pass_percent?: number;
  checks: SuitabilityCheck[];
  blockers: string[];
  limitations: string[];
  next_actions: string[];
  recommendation: string;
  run_ids: string[];
};

type SuitabilityTarget = {
  id: string;
  label: string;
  scope: string;
  provider: { name: string; confidence: number; source: string; region?: string; zone?: string; instance_type?: string };
  system: { os?: string; cpu?: string; logical_cores?: number; memory_bytes?: number };
  evidence_summary: { accepted_runs: number; rejected_runs: { run_id: string; reason: string }[]; suites: string[]; freshness_days: number };
  levels: Record<string, SuitabilityScenario[]>;
  provider_assessment: {
    status: "not-rated";
    claim: string;
    same_product_targets: number;
    measurement_windows: number;
    observed_suites: string[];
    criteria: { label: string; satisfied: boolean }[];
    gaps: string[];
  };
};

type ProviderMetricCohort = {
  contract_id: string;
  key: string;
  label: string;
  suite: string;
  direction: "higher" | "lower";
  unit: string;
  profile: string;
  methodology_version: string;
  topology_scope: TopologyScope | "single-target";
  topology_evidence: "single-target" | "operator-declared" | "independently-derived" | "contradicted" | "unavailable";
  status: "comparable" | "observational";
  reasons: string[];
  sample_count: number;
  target_count: number;
  window_count: number;
  windows: string[];
  run_ids: string[];
  latest_observed_at?: string;
  statistics: {
    median: number;
    p10: number;
    p90: number;
    minimum: number;
    maximum: number;
    best: number;
    worst: number;
    relative_spread_percent?: number;
    stability: "stable" | "moderate" | "variable" | "insufficient-sampling";
  };
};

type ProviderObservationGroup = {
  id: string;
  provider: string;
  instance_type: string;
  region: string;
  operating_system: string;
  scope: string;
  comparison_status: "observational" | "partial" | "sampling-ready";
  rating_status: "not-rated";
  target_ids: string[];
  target_count: number;
  windows: string[];
  window_count: number;
  observed_suites: string[];
  criteria: { label: string; satisfied: boolean }[];
  gaps: string[];
  metric_cohorts: ProviderMetricCohort[];
};

type ProviderObservations = {
  version: string;
  rating_status: "not-rated";
  window_definition: string;
  minimum_comparable_sampling: { samples: number; targets: number; windows: number };
  policy: {
    exact_profile_and_methodology: boolean;
    exact_pair_topology: boolean;
    exact_pair_topology_evidence: boolean;
    cross_sku_aggregation: boolean;
    cross_region_aggregation: boolean;
    cross_os_aggregation: boolean;
    provider_ranking: boolean;
  };
  groups: ProviderObservationGroup[];
  excluded_targets: { target_id: string; reason: string }[];
};

type SuitabilityReport = {
  engine_version: string;
  requirements_version: string;
  generated_at: string;
  policy: { missing_evidence_is_zero: boolean; composite_provider_score: boolean; target_scoped: boolean; max_evidence_age_days: number };
  levels: Record<string, { label: string; description: string }>;
  targets: SuitabilityTarget[];
  provider_observations: ProviderObservations;
};

type Dashboard = {
  version: string;
  system: { inventory: Inventory; provider: Provider };
  runs: Run[];
  sessions: Session[];
  suitability?: SuitabilityReport;
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
    database?: Record<string, {
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
    web?: Record<string, {
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

function suitabilityVerdictLabel(verdict: SuitabilityScenario["verdict"]) {
  return {
    insufficient: "Insufficient evidence",
    "below-requirement": "Below requirement",
    "conditional-fit": "Conditional fit",
    suitable: "Suitable",
  }[verdict];
}

function formatRequirementValue(value: number, unit: string) {
  if (unit === "B") return formatBytes(value);
  if (unit === "B/s") return `${formatBytes(value)}/s`;
  if (unit === "bit/s") return `${(value / 1_000_000).toFixed(value >= 1_000_000_000 ? 0 : 1)} Mb/s`;
  if (unit === "boolean") return value === 1 ? "Verified" : "Not detected";
  if (["ms", "%"].includes(unit)) return `${value.toFixed(2)} ${unit}`;
  if (["IOPS", "TPS", "req/s", "events/s"].includes(unit)) return `${Math.round(value).toLocaleString()} ${unit}`;
  return `${value.toLocaleString()} ${unit}`;
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
  const [selectedSuitabilityTarget, setSelectedSuitabilityTarget] = useState("controller");
  const [selectedRequirementLevel, setSelectedRequirementLevel] = useState("essential");
  const [selectedSuitabilityScenario, setSelectedSuitabilityScenario] = useState("web-app");
  const [selectedProviderContract, setSelectedProviderContract] = useState("");
  const [selectedExecutionTarget, setSelectedExecutionTarget] = useState("local");
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const [selectedTopologyScope, setSelectedTopologyScope] = useState<TopologyScope>("undeclared");
  const [pairing, setPairing] = useState<{
    id: string;
    join_token: string;
    expires_at: string;
    topology: PairTopology;
  } | null>(null);

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
  const selectedNetworkMethodology = dashboard?.profiles.network?.[selectedNetworkProfile]?.methodology_version;
  const selectedSessionNetworkReady = Boolean(selectedSession)
    && ["target", "generator"].every((role) => {
      const agent = selectedSession?.agents.find((item) => item.role === role);
      const capabilities = agent?.system.inventory?.capabilities || {};
      return capabilities.iperf3
        && (!["network-v4", "network-v5", "network-v6"].includes(selectedNetworkMethodology || "")
          || (capabilities.iproute2
            && capabilities.ethtool
            && capabilities.tcp_congestion_control
            && (selectedNetworkMethodology !== "network-v6" || capabilities.tracepath)));
    });
  const networkMeasurements = latestNetwork?.result?.measurements || [];
  const latencyMeasurements = latestNetwork?.result?.latency_measurements || [];
  const udpMeasurements = latestNetwork?.result?.udp_measurements || [];
  const bidirectionalMeasurements = latestNetwork?.result?.bidirectional_measurements || [];
  const pathMeasurements = latestNetwork?.result?.path_measurements || [];
  const networkAnalysis = latestNetwork?.result?.analysis?.directions || [];
  const networkCounterDeltas = latestNetwork?.result?.analysis?.interface_counter_deltas || [];
  const networkPathStability = latestNetwork?.result?.analysis?.path_stability || [];
  const networkValidity = latestNetwork?.result?.analysis?.validity;
  const maxNetworkRate = Math.max(1, ...networkMeasurements.map((item) => item.metrics.received_bits_per_second || 0));
  const activeDatabase = dashboard?.runs.find(
    (run) => run.suite === "database" && ["queued", "running"].includes(run.status),
  );
  const latestDatabase = dashboard?.runs.find(
    (run) => run.suite === "database" && run.status === "completed" && run.result?.database_measurements?.length,
  );
  const databaseMeasurements = latestDatabase?.result?.database_measurements || [];
  const databaseProfile = dashboard?.profiles.database?.[selectedDatabaseProfile];
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
  const webProfile = dashboard?.profiles.web?.[selectedWebProfile];
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
  const domainCounts = useMemo(() => {
    const domains = dashboard?.profiles.domains || [];
    return {
      total: domains.length,
      available: domains.filter((domain) => domain.status === "available").length,
      partial: domains.filter((domain) => domain.status === "partial").length,
      roadmap: domains.filter((domain) => domain.status === "roadmap").length,
    };
  }, [dashboard?.profiles.domains]);
  const suitabilityTarget = dashboard?.suitability?.targets.find((target) => target.id === selectedSuitabilityTarget)
    || dashboard?.suitability?.targets[0];
  const suitabilityScenarios = suitabilityTarget?.levels[selectedRequirementLevel] || [];
  const suitabilityScenario = suitabilityScenarios.find((scenario) => scenario.id === selectedSuitabilityScenario)
    || suitabilityScenarios[0];
  const suitabilityCounts = {
    suitable: suitabilityScenarios.filter((scenario) => scenario.verdict === "suitable").length,
    conditional: suitabilityScenarios.filter((scenario) => scenario.verdict === "conditional-fit").length,
    below: suitabilityScenarios.filter((scenario) => scenario.verdict === "below-requirement").length,
    insufficient: suitabilityScenarios.filter((scenario) => scenario.verdict === "insufficient").length,
  };
  const providerObservations = dashboard?.suitability?.provider_observations;
  const providerGroups = providerObservations?.groups || [];
  const providerContracts = Array.from(new Map(
    providerGroups.flatMap((group) => group.metric_cohorts).map((metric) => [metric.contract_id, metric]),
  ).values()).sort((left, right) => `${left.suite}.${left.label}.${left.profile}`.localeCompare(`${right.suite}.${right.label}.${right.profile}`));
  const activeProviderContract = providerContracts.find((metric) => metric.contract_id === selectedProviderContract)
    || providerContracts[0];
  const comparableMetricCount = providerGroups.reduce(
    (total, group) => total + group.metric_cohorts.filter((metric) => metric.status === "comparable").length,
    0,
  );

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
        body: JSON.stringify({
          label: "Provider paired assessment",
          topology: {
            scope: selectedTopologyScope,
            source: selectedTopologyScope === "undeclared" ? "unavailable" : "operator-declared",
          },
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Unable to create a pairing session");
      setPairing(payload);
      setSelectedSessionId(payload.id);
      setNotice(
        selectedTopologyScope === "undeclared"
          ? "A 30-minute pairing session is ready. Its results remain observational until topology is declared."
          : `A 30-minute ${selectedTopologyScope} pairing session is ready for two provider Agents.`,
      );
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
    ["providers", "Provider Comparison", "09"],
    ["history", "History", "10"],
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
            <section className="section-intro"><div><span className="section-kicker">DISTRIBUTED ASSESSMENT</span><h2>Keep the control path separate from benchmark data traffic.</h2><p>The Controller schedules guarded tasks and stores evidence. TCP, adaptive-rate UDP, and ICMP measurements flow directly between the two provider Agents.</p></div><div className="runner-actions"><label><span>PROFILE</span><select value={selectedNetworkProfile} onChange={(event) => setSelectedNetworkProfile(event.target.value)} disabled={Boolean(activeNetwork)}>{Object.entries(dashboard?.profiles.network || {}).map(([id, profile]) => <option key={id} value={id}>{profile.label}</option>)}</select></label><label><span>PAIR TOPOLOGY</span><select value={selectedTopologyScope} onChange={(event) => setSelectedTopologyScope(event.target.value as TopologyScope)}>{TOPOLOGY_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label><button className="button primary" onClick={createPairing} disabled={busy}>New session</button></div></section>
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
            {pairing && <section className="pairing-card"><div><span>SHORT-LIVED JOIN CREDENTIAL</span><strong>{pairing.id}</strong><small>{pairing.topology.scope} · {pairing.topology.source} · verification {pairing.topology.verification.status} · expires {new Date(pairing.expires_at).toLocaleTimeString("en-US")}</small></div><code>{pairing.join_token}</code></section>}
            {pairing && <section className="panel agent-commands"><div><span className="section-kicker">RUN ON PROVIDER VMS</span><h3>Start one persistent worker on each clean machine</h3></div><code>cloudmark agent --controller https://CONTROLLER --session {pairing.id} --token {pairing.join_token} --role target --advertise-address VM_A_IP</code><code>cloudmark agent --controller https://CONTROLLER --session {pairing.id} --token {pairing.join_token} --role generator --advertise-address VM_B_IP</code><p>Use <strong>--allow-http</strong> only when Controller access is restricted to a trusted private management network.</p></section>}
            <section className="panel session-panel">
              <div className="panel-head"><div><span className="section-kicker">AGENT CONTROL PLANE</span><h3>Pairing readiness</h3></div><label className="compact-select"><span>SESSION</span><select value={selectedSession?.id || ""} onChange={(event) => setSelectedSessionId(event.target.value)}>{dashboard?.sessions.map((session) => <option key={session.id} value={session.id}>{session.label} · {session.topology.scope} / {session.topology.verification.status} · {session.status}</option>)}</select></label></div>
              {selectedSession ? <div className="agent-roster">{["target", "generator"].map((role) => { const agent = selectedSession.agents.find((item) => item.role === role); const capabilities = agent?.system.inventory?.capabilities || {}; const standardProfile = ["network-v4", "network-v5", "network-v6"].includes(selectedNetworkMethodology || ""); const agentReady = capabilities.iperf3 && (!standardProfile || (capabilities.iproute2 && capabilities.ethtool && capabilities.tcp_congestion_control && (selectedNetworkMethodology !== "network-v6" || capabilities.tracepath))); return <article key={role} className={agent && agentReady ? "connected" : "waiting"}><span>{role.toUpperCase()}</span><strong>{agent?.name || `Waiting for ${role}`}</strong><small>{agent ? `${agent.endpoint.address || "No advertised IP"} · ${agentReady ? standardProfile ? "Network standard ready" : "iperf3 ready" : "Network prerequisites missing"}` : "Join command has not connected"}</small></article>; })}</div> : <div className="empty-row">Create a session, then connect both provider agents.</div>}
              <div className="session-actions"><p><strong>{selectedSession?.status !== "ready" ? "Two agents required" : selectedSessionNetworkReady ? "Ready to measure" : "Network prerequisites missing"}</strong><small>Network v6 requires iperf3, iproute2, tracepath, ethtool, and Linux TCP-control evidence on both Agents.</small></p><button className="button primary" onClick={startNetwork} disabled={busy || Boolean(activeNetwork) || selectedSession?.status !== "ready" || !selectedSessionNetworkReady}>Run network assessment</button></div>
            </section>
            {activeNetwork && <section className="panel run-progress" aria-live="polite"><div><span className="section-kicker">ACTIVE NETWORK RUN / {activeNetwork.id}</span><strong>{activeNetwork.current_job || activeNetwork.phase || "Waiting for agents"}</strong><small>{activeNetwork.completed_steps || 0} of {activeNetwork.total_steps || 1} measurements · {Math.round((activeNetwork.progress || 0) * 100)}%</small></div><div className="progress-track"><i style={{ width: `${Math.max(2, (activeNetwork.progress || 0) * 100)}%` }} /></div><button className="button danger" onClick={cancelNetwork} disabled={busy || activeNetwork.cancel_requested}>{activeNetwork.cancel_requested ? "Cancelling" : "Cancel run"}</button></section>}
            <section className="panel network-results">
              <div className="panel-head"><div><span className="section-kicker">LATEST VERIFIED RUN</span><h3>Peer TCP throughput</h3></div><span className="run-id">{latestNetwork?.id || "NO RUN YET"}</span></div>
              {networkMeasurements.length ? <div className="bar-chart">{networkMeasurements.map((measurement, index) => { const rate = measurement.metrics.received_bits_per_second || 0; return <div className="bar-row" key={`${measurement.direction}-${measurement.streams}-${index}`}><span>{measurement.sender.name} → {measurement.receiver.name} · P{measurement.streams}</span><div><i style={{ width: `${Math.max(3, (rate / maxNetworkRate) * 100)}%` }} /></div><strong>{(rate / 1_000_000).toFixed(1)} Mb/s</strong></div>; })}</div> : <div className="empty-chart"><div className="chart-grid" /><strong>No peer result yet</strong><p>Connect two agents and run a profile to establish a directional baseline.</p></div>}
            </section>
            {(pathMeasurements.length > 0 || latencyMeasurements.length > 0 || udpMeasurements.length > 0 || bidirectionalMeasurements.length > 0) && (
              <section className="network-evidence-grid">
                {pathMeasurements.length > 0 && <article className="panel network-evidence-card">
                  <div className="panel-head"><div><span className="section-kicker">PATH IDENTITY</span><h3>Route and MTU evidence</h3></div><span className="run-id">{networkValidity?.route_evidence_status?.toUpperCase() || "UNKNOWN"}</span></div>
                  <div className="evidence-rows">{pathMeasurements.map((item) => <div key={item.direction}><span>{item.sender.name} → {item.receiver.name}</span><strong>{item.evidence.interface?.name || "Interface unavailable"} · MTU {item.evidence.interface?.mtu_bytes ?? "—"}</strong><small>{item.evidence.path_mtu?.value_bytes ? `Path MTU ${item.evidence.path_mtu.value_bytes} bytes` : item.evidence.reason || "Path MTU not observed"}</small></div>)}</div>
                </article>}
                {pathMeasurements.length > 0 && <article className="panel network-evidence-card">
                  <div className="panel-head"><div><span className="section-kicker">BOUNDED PATH TRACE</span><h3>Endpoint class and route stability</h3></div><span className="run-id">{networkValidity?.path_trace_evidence_status?.toUpperCase() || "UNKNOWN"}</span></div>
                  <div className="evidence-rows">{pathMeasurements.map((item) => { const trace = item.evidence.path_trace; const stability = networkPathStability.find((value) => value.direction === item.direction); const observedHops = trace?.hops.filter((hop) => hop.state === "observed").length || 0; return <div key={item.direction}><span>{item.sender.name} → {item.receiver.name} · {trace?.destination_address_class || "unclassified"}</span><strong>{trace?.reached_destination ? `Destination reached in ${observedHops} observed hop${observedHops === 1 ? "" : "s"}` : "Destination not reached by bounded trace"}</strong><small>Boundary route {stability?.route_stable === true ? "stable" : stability?.route_stable === false ? "changed" : "unavailable"} · trace sequence {stability?.trace_stable === true ? "stable" : stability?.trace_stable === false ? "changed" : "unavailable"}</small></div>; })}</div>
                  <p className="method-note">A numeric address class and up to eight observed IP hops do not prove administrative ownership or public Internet transit. Trace-sequence changes are recorded as evidence and may reflect normal ECMP behavior.</p>
                </article>}
                {networkCounterDeltas.length > 0 && <article className="panel network-evidence-card">
                  <div className="panel-head"><div><span className="section-kicker">INTERFACE WINDOW</span><h3>Pre/post packet counters</h3></div><span className="run-id">{networkValidity?.interface_counter_evidence_status?.toUpperCase() || "UNKNOWN"}</span></div>
                  <div className="evidence-rows">{networkCounterDeltas.map((item) => <div key={item.direction}><span>{item.sender?.name || item.direction} · {item.interface || "Interface unavailable"}</span><strong>{item.status === "observed" ? `${(item.counters?.rx_packets || 0).toLocaleString()} RX · ${(item.counters?.tx_packets || 0).toLocaleString()} TX packets` : "Counter delta unavailable"}</strong><small>{item.status === "observed" ? `${formatBytes(item.counters?.rx_bytes)} RX · ${formatBytes(item.counters?.tx_bytes)} TX · ${item.total_dropped || 0} drops · ${item.total_errors || 0} errors` : item.reason}</small></div>)}</div>
                  <p className="method-note">Deltas include all traffic on the route-derived interface during the benchmark window. Observed drops and errors remain visible evidence and do not invalidate an otherwise complete Run.</p>
                </article>}
                {pathMeasurements.length > 0 && <article className="panel network-evidence-card">
                  <div className="panel-head"><div><span className="section-kicker">NIC AND TCP CONTROL</span><h3>Driver and offload evidence</h3></div><span className="run-id">{networkValidity?.nic_evidence_status?.toUpperCase() || "UNKNOWN"}</span></div>
                  <div className="evidence-rows">{pathMeasurements.map((item) => { const features = Object.values(item.evidence.interface?.offloads?.features || {}); const enabled = features.filter((feature) => feature.enabled).length; return <div key={item.direction}><span>{item.sender.name} · {item.evidence.interface?.name || "Interface unavailable"}</span><strong>{item.evidence.interface?.driver?.driver || "Driver unavailable"} · TCP {item.evidence.tcp?.congestion_control?.algorithm || "unknown"}</strong><small>{item.evidence.interface?.offloads?.status === "observed" ? `${enabled} of ${features.length} selected offloads enabled` : item.evidence.interface?.offloads?.reason || "Offload state unavailable"}</small></div>; })}</div>
                  <p className="method-note">CloudMark reads the active egress interface only. It never enables, disables, or otherwise changes NIC offloads or TCP congestion control.</p>
                </article>}
                {networkAnalysis.length > 0 && <article className="panel network-evidence-card">
                  <div className="panel-head"><div><span className="section-kicker">MEASUREMENT VALIDITY</span><h3>Generator headroom</h3></div><span className="run-id">{networkValidity?.comparison_eligible ? "COMPARABLE" : "NOT COMPARABLE"}</span></div>
                  <div className="evidence-rows">{networkAnalysis.map((item) => <div key={item.direction}><span>{item.direction}</span><strong>{item.generator_headroom?.status || "unknown"}</strong><small>{item.generator_headroom?.peak_cpu_percent?.toFixed(1) ?? "—"}% peak Generator CPU · {item.generator_headroom?.stream_scaling_gain_percent?.toFixed(1) ?? "—"}% P1→P16 gain</small></div>)}</div>
                  <p className="method-note">CloudMark excludes network-v6 evidence from suitability and provider comparison when route identity and stability, bounded destination-reaching traces, NIC/TCP-control evidence, the interface-counter window, or Generator headroom is incomplete.</p>
                </article>}
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
              {[["TCP", "1 / 4 / 8 / 16 streams", "AVAILABLE"], ["UDP", "25 / 50 / 90% adaptive sweep", "AVAILABLE IN STANDARD"], ["TRACE", "address class · ≤8 numeric hops", "READ-ONLY IN NETWORK-V6"], ["NIC", "driver · offloads · congestion control", "READ-ONLY IN STANDARD"], ["COUNTERS", "pre/post bytes · packets · drops · errors", "READ-ONLY IN NETWORK-V6"], ["VALIDITY", "Route · trace · NIC · counters · Generator", "ENFORCED IN NETWORK-V6"]].map(([name, detail, state]) => <article key={name}><span>{name}</span><strong>{detail}</strong><small>{state}</small></article>)}
            </section>
          </div>
        )}

        {activeView === "database" && (
          <div className="view database-view">
            <section className="section-intro">
              <div><span className="section-kicker">TWO-AGENT DATABASE ASSESSMENT</span><h2>Measure the database service, storage path, CPU, and provider network together.</h2><p>CloudMark creates an isolated PostgreSQL cluster on the Target and dispatches exact pgbench workloads from the Generator. The Controller stores evidence but never carries transaction traffic.</p></div>
              <div className="runner-actions"><label><span>PROFILE</span><select value={selectedDatabaseProfile} onChange={(event) => setSelectedDatabaseProfile(event.target.value)} disabled={Boolean(activeDatabase)}>{Object.entries(dashboard?.profiles.database || {}).map(([id, profile]) => <option key={id} value={id}>{profile.label} · ≈ {profile.estimated_minutes} min</option>)}</select></label><label><span>PAIR TOPOLOGY</span><select value={selectedTopologyScope} onChange={(event) => setSelectedTopologyScope(event.target.value as TopologyScope)}>{TOPOLOGY_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label><button className="button primary" onClick={createPairing} disabled={busy}>New session</button></div>
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
            {pairing && <section className="pairing-card"><div><span>SHORT-LIVED JOIN CREDENTIAL</span><strong>{pairing.id}</strong><small>{pairing.topology.scope} · {pairing.topology.source} · verification {pairing.topology.verification.status} · expires {new Date(pairing.expires_at).toLocaleTimeString("en-US")}</small></div><code>{pairing.join_token}</code></section>}
            {pairing && <section className="panel agent-commands"><div><span className="section-kicker">RUN ON PROVIDER VMS</span><h3>Target hosts PostgreSQL; Generator runs pgbench</h3></div><code>cloudmark agent --controller https://CONTROLLER --session {pairing.id} --token {pairing.join_token} --role target --advertise-address VM_A_IP</code><code>cloudmark agent --controller https://CONTROLLER --session {pairing.id} --token {pairing.join_token} --role generator --advertise-address VM_B_IP</code><p>Install the <strong>database</strong> pack on both machines before starting their Agents.</p></section>}
            <section className="panel session-panel">
              <div className="panel-head"><div><span className="section-kicker">PAIRED EXECUTION</span><h3>Database readiness</h3></div><label className="compact-select"><span>SESSION</span><select value={selectedSession?.id || ""} onChange={(event) => setSelectedSessionId(event.target.value)}>{dashboard?.sessions.map((session) => <option key={session.id} value={session.id}>{session.label} · {session.topology.scope} / {session.topology.verification.status} · {session.status}</option>)}</select></label></div>
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
              <div className="runner-actions"><label><span>PROFILE</span><select value={selectedWebProfile} onChange={(event) => setSelectedWebProfile(event.target.value)} disabled={Boolean(activeWeb)}>{Object.entries(dashboard?.profiles.web || {}).map(([id, profile]) => <option key={id} value={id}>{profile.label} · ≈ {profile.estimated_minutes} min</option>)}</select></label><label><span>PAIR TOPOLOGY</span><select value={selectedTopologyScope} onChange={(event) => setSelectedTopologyScope(event.target.value as TopologyScope)}>{TOPOLOGY_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label><button className="button primary" onClick={createPairing} disabled={busy}>New session</button></div>
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
            {pairing && <section className="pairing-card"><div><span>SHORT-LIVED JOIN CREDENTIAL</span><strong>{pairing.id}</strong><small>{pairing.topology.scope} · {pairing.topology.source} · verification {pairing.topology.verification.status} · expires {new Date(pairing.expires_at).toLocaleTimeString("en-US")}</small></div><code>{pairing.join_token}</code></section>}
            {pairing && <section className="panel agent-commands"><div><span className="section-kicker">RUN ON PROVIDER VMS</span><h3>Target hosts Nginx; Generator runs ApacheBench</h3></div><code>cloudmark agent --controller https://CONTROLLER --session {pairing.id} --token {pairing.join_token} --role target --advertise-address VM_A_IP</code><code>cloudmark agent --controller https://CONTROLLER --session {pairing.id} --token {pairing.join_token} --role generator --advertise-address VM_B_IP</code><p>Install the <strong>web</strong> pack on both machines and open TCP 58080 and 58443 only between the paired machines.</p></section>}
            <section className="panel session-panel">
              <div className="panel-head"><div><span className="section-kicker">PAIRED EXECUTION</span><h3>Web assessment readiness</h3></div><label className="compact-select"><span>SESSION</span><select value={selectedSession?.id || ""} onChange={(event) => setSelectedSessionId(event.target.value)}>{dashboard?.sessions.map((session) => <option key={session.id} value={session.id}>{session.label} · {session.topology.scope} / {session.topology.verification.status} · {session.status}</option>)}</select></label></div>
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
            <section className="section-intro">
              <div><span className="section-kicker">EVIDENCE-GATED SUITABILITY</span><h2>Classify one observed target against explicit workload requirements.</h2><p>CloudMark evaluates {domainCounts.total || 17} technical domains without converting missing evidence into zero. Every check retains its Run ID, profile, methodology, observation time, and evidence status.</p></div>
              <div className="runner-actions suitability-selectors">
                <label><span>TARGET</span><select value={suitabilityTarget?.id || "controller"} onChange={(event) => setSelectedSuitabilityTarget(event.target.value)}>{dashboard?.suitability?.targets.map((target) => <option key={target.id} value={target.id}>{target.label} · {target.provider.name}</option>)}</select></label>
                <label><span>REQUIREMENT LEVEL</span><select value={selectedRequirementLevel} onChange={(event) => setSelectedRequirementLevel(event.target.value)}>{Object.entries(dashboard?.suitability?.levels || {}).map(([id, level]) => <option key={id} value={id}>{level.label}</option>)}</select></label>
              </div>
            </section>
            <section className="suitability-summary-grid">
              <article className="panel"><span>TARGET SCOPE</span><strong>{suitabilityTarget?.scope === "single-target-observation" ? "Single target" : "Unavailable"}</strong><small>{suitabilityTarget?.provider.name || "Unknown provider"} · {suitabilityTarget?.provider.instance_type || "SKU unavailable"}</small></article>
              <article className="panel"><span>ACCEPTED EVIDENCE</span><strong>{suitabilityTarget?.evidence_summary.accepted_runs || 0} runs</strong><small>{suitabilityTarget?.evidence_summary.suites.join(" · ") || "No completed suite"}</small></article>
              <article className="panel conditional"><span>CONDITIONAL FITS</span><strong>{suitabilityCounts.conditional}</strong><small>{dashboard?.suitability?.levels[selectedRequirementLevel]?.label || "Selected"} requirement contract</small></article>
              <article className="panel"><span>PROVIDER CLAIM</span><strong>Not rated</strong><small>{suitabilityTarget?.provider_assessment.claim || "Provider-wide evidence is unavailable."}</small></article>
            </section>
            <section className="scenario-evaluation-grid" aria-label="Workload suitability classifications">
              {suitabilityScenarios.map((scenario, index) => (
                <button key={scenario.id} className={`scenario-evaluation-card ${scenario.verdict} ${suitabilityScenario?.id === scenario.id ? "selected" : ""}`} onClick={() => setSelectedSuitabilityScenario(scenario.id)}>
                  <div><span>{String(index + 1).padStart(2, "0")}</span><i /></div>
                  <h3>{scenario.label}</h3>
                  <p>{suitabilityVerdictLabel(scenario.verdict)}</p>
                  <footer><span>{scenario.coverage_percent.toFixed(0)}% evidence coverage</span><strong>{scenario.measured_pass_percent == null ? "UNMEASURED" : `${scenario.measured_pass_percent.toFixed(0)}% measured gates`}</strong></footer>
                </button>
              ))}
            </section>
            {suitabilityScenario && <section className={`panel suitability-detail ${suitabilityScenario.verdict}`}>
              <div className="panel-head"><div><span className="section-kicker">{dashboard?.suitability?.requirements_version} / {selectedRequirementLevel.toUpperCase()}</span><h3>{suitabilityScenario.label}</h3></div><span className={`suitability-verdict ${suitabilityScenario.verdict}`}>{suitabilityVerdictLabel(suitabilityScenario.verdict)}</span></div>
              <p className="suitability-recommendation">{suitabilityScenario.recommendation}</p>
              <div className="suitability-checks">
                <div className="suitability-check-head"><span>REQUIREMENT</span><span>OBSERVED</span><span>GATE</span><span>STATUS / SOURCE</span></div>
                {suitabilityScenario.checks.map((check) => <div className={`suitability-check ${check.status}`} key={check.key}>
                  <strong>{check.label}</strong>
                  <span>{check.evidence ? formatRequirementValue(check.evidence.value, check.unit) : check.status === "stale" ? "Stale evidence" : "Unavailable"}</span>
                  <span>{check.operator} {formatRequirementValue(check.threshold, check.unit)}</span>
                  <span><i>{check.status}</i><small>{check.evidence?.run_id ? `${check.evidence.run_id} · ${check.evidence.profile}` : check.evidence?.source || "Required executor has not produced evidence"}</small></span>
                </div>)}
              </div>
              <div className="suitability-guidance-grid">
                <article><span>BLOCKING EVIDENCE</span>{suitabilityScenario.blockers.length ? <ul>{suitabilityScenario.blockers.map((item) => <li key={item}>{item}</li>)}</ul> : <p>No unimplemented hard blocker; all listed metric gates still require valid evidence.</p>}</article>
                <article><span>INTERPRETATION LIMITS</span>{suitabilityScenario.limitations.length ? <ul>{suitabilityScenario.limitations.map((item) => <li key={item}>{item}</li>)}</ul> : <p>No additional limitation is registered for this methodology.</p>}</article>
                <article><span>NEXT EVIDENCE</span><ul>{suitabilityScenario.next_actions.map((item) => <li key={item}>{item}</li>)}</ul></article>
              </div>
            </section>}
            <section className="panel provider-readiness-panel">
              <div className="panel-head"><div><span className="section-kicker">PROVIDER EVALUATION READINESS</span><h3>{suitabilityTarget?.provider.name || "Unknown provider"}</h3></div><span className="run-id">INSTANCE OBSERVATION ONLY</span></div>
              <div className="provider-readiness-body"><div><strong>{suitabilityTarget?.provider_assessment.same_product_targets || 0}</strong><small>same-product targets</small></div><div><strong>{suitabilityTarget?.provider_assessment.measurement_windows || 0}</strong><small>measurement windows</small></div><div className="provider-criteria">{suitabilityTarget?.provider_assessment.criteria.map((criterion) => <p key={criterion.label} className={criterion.satisfied ? "met" : "missing"}><i>{criterion.satisfied ? "✓" : "—"}</i><span>{criterion.label}</span></p>)}</div></div>
              <p className="method-note">CloudMark will not publish a provider-wide score from one machine or one time window. Security, reliability, control-plane, cost, repeated-window, and same-SKU evidence remain independent gates.</p>
            </section>
          </div>
        )}

        {activeView === "providers" && (
          <div className="view providers-view">
            <section className="section-intro">
              <div><span className="section-kicker">REPEATED-WINDOW OBSERVATIONS</span><h2>Compare like with like without inventing a provider score.</h2><p>CloudMark separates cohorts by provider, SKU, region, operating system, profile, methodology, and paired topology. Descriptive statistics become comparable only after the minimum target, window, and sample contract is met.</p></div>
              <div className="runner-actions provider-contract-selector">
                <label><span>METRIC CONTRACT</span><select value={activeProviderContract?.contract_id || ""} onChange={(event) => setSelectedProviderContract(event.target.value)} disabled={!providerContracts.length}>
                  {!providerContracts.length && <option value="">No repeated evidence</option>}
                  {providerContracts.map((metric) => <option key={metric.contract_id} value={metric.contract_id}>{metric.label} · {metric.profile} · {metric.topology_scope} / {metric.topology_evidence}</option>)}
                </select></label>
              </div>
            </section>
            <section className="provider-summary-grid">
              <article className="panel"><span>EXACT COHORTS</span><strong>{providerGroups.length}</strong><small>provider + SKU + region + OS</small></article>
              <article className="panel"><span>COMPARABLE METRICS</span><strong>{comparableMetricCount}</strong><small>minimum sampling contract satisfied</small></article>
              <article className="panel"><span>EXCLUDED TARGETS</span><strong>{providerObservations?.excluded_targets.length || 0}</strong><small>missing identity, SKU, or fresh evidence</small></article>
              <article className="panel caution"><span>PROVIDER RATING</span><strong>Not rated</strong><small>operational and cost gates remain unavailable</small></article>
            </section>
            <section className="panel comparison-contract-panel">
              <div className="panel-head"><div><span className="section-kicker">COMPARISON CONTRACT</span><h3>{activeProviderContract?.label || "No compatible metric evidence yet"}</h3></div><span className="run-id">{providerObservations?.version || "provider-observations-v3"}</span></div>
              <div className="comparison-contract-grid">
                <div><span>PROFILE</span><strong>{activeProviderContract?.profile || "Unavailable"}</strong></div>
                <div><span>METHODOLOGY</span><strong>{activeProviderContract?.methodology_version || "Unavailable"}</strong></div>
                <div><span>PAIR TOPOLOGY</span><strong>{activeProviderContract?.topology_scope || "Unavailable"}</strong></div>
                <div><span>TOPOLOGY EVIDENCE</span><strong>{activeProviderContract?.topology_evidence || "Unavailable"}</strong></div>
                <div><span>DIRECTION</span><strong>{activeProviderContract ? `${activeProviderContract.direction} is better` : "Unavailable"}</strong></div>
                <div><span>MINIMUM SAMPLE</span><strong>{providerObservations ? `${providerObservations.minimum_comparable_sampling.samples} runs / ${providerObservations.minimum_comparable_sampling.targets} targets / ${providerObservations.minimum_comparable_sampling.windows} days` : "Unavailable"}</strong></div>
              </div>
              <p className="method-note">A measurement window is one UTC calendar day. Paired runs with different scopes or topology evidence classes are never merged, and contradictory topology stays observational. P10, median, P90, actual best/worst, sample count, target count, and relative spread remain descriptive evidence; CloudMark does not rank providers.</p>
            </section>
            {providerGroups.length ? <section className="provider-comparison-grid" aria-label="Provider cohort observations">
              {providerGroups.map((group) => {
                const metric = group.metric_cohorts.find((item) => item.contract_id === activeProviderContract?.contract_id);
                return <article className={`panel provider-comparison-card ${metric?.status || "missing"}`} key={group.id}>
                  <header><div><span>{group.provider}</span><h3>{group.instance_type}</h3><small>{group.region} · {group.operating_system}</small></div><i>{metric?.status === "comparable" ? "Comparable" : metric ? "Observation only" : "No matching evidence"}</i></header>
                  {metric ? <>
                    <div className="provider-stat-grid">
                      <div><span>MEDIAN</span><strong>{formatRequirementValue(metric.statistics.median, metric.unit)}</strong></div>
                      <div><span>P10 / P90</span><strong>{formatRequirementValue(metric.statistics.p10, metric.unit)} / {formatRequirementValue(metric.statistics.p90, metric.unit)}</strong></div>
                      <div><span>WORST</span><strong>{formatRequirementValue(metric.statistics.worst, metric.unit)}</strong></div>
                      <div><span>STABILITY</span><strong>{metric.statistics.stability.replaceAll("-", " ")}</strong><small>{metric.statistics.relative_spread_percent == null ? "relative spread unavailable" : `${metric.statistics.relative_spread_percent.toFixed(2)}% P10–P90 spread`}</small></div>
                    </div>
                    <div className="provider-sample-row"><span>{metric.sample_count} samples</span><span>{metric.target_count} targets</span><span>{metric.window_count} UTC days</span></div>
                    {metric.reasons.length ? <ul className="provider-comparison-reasons">{metric.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul> : <p className="provider-comparison-ready">Minimum descriptive comparison sampling is satisfied. Provider rating remains disabled.</p>}
                    <footer><span>{group.comparison_status.replaceAll("-", " ")}</span><code>{metric.run_ids.length} traceable Run IDs</code></footer>
                  </> : <div className="provider-metric-empty"><strong>No exact contract match</strong><p>This cohort may have another profile or methodology. CloudMark will not merge it into the selected comparison.</p></div>}
                </article>;
              })}
            </section> : <section className="panel provider-empty-state"><strong>No provider cohort is eligible for aggregation yet.</strong><p>Fresh valid benchmark evidence and a provider/SKU identity are required before a cohort appears. Targets excluded from aggregation remain visible in Workload Suitability.</p></section>}
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
