# HTTP/2 load methodology

`web-http2-load-v1` measures a packaged dynamic application through Nginx over
HTTPS and HTTP/2 between two authenticated provider Agents. The Target hosts
the service; the Generator runs `h2load`; the Controller carries only control
and evidence traffic.

## Fixed workload shapes

The `web-peer-http2` profile uses the exact `/api/v2/dynamic` 1 KiB response on
TCP 58443 with the ephemeral CloudMark certificate:

| Job | HTTP/2 clients | Native threads | Max streams/session | Requests |
|---|---:|---:|---:|---:|
| `h2-dynamic-c1-m1` | 1 | 1 | 1 | 2,000 |
| `h2-dynamic-c8-m16` | 8 | 2 | 16 | 10,000 |
| `h2-dynamic-c32-m32` | 32 | 4 | 32 | 20,000 |

The Agent independently rejects every other scheme, address, port, path,
client count, thread count, stream count, request count, header, body, or URI.
Request-count workloads are used instead of an unbounded duration mode. Each
job has a 90-second child-process budget plus the standard Controller-contact
watchdog.

## Evidence

CloudMark records total/started/done/succeeded/failed/errored/timed-out
requests, response status classes, elapsed time, aggregate requests per second,
transfer rate, and body/header byte counts.

Every request is also written to a generated h2load TSV log below
`h2load-logs/task_*`. The Agent reads at most 8 MiB and 25,000 rows, requires an
exact row count, and computes nearest-rank P50/P95/P99/maximum latency from
successful request durations. Failed status rows remain visible and never
become successful latency samples. The complete log directory is removed after
success, failure, timeout, cancellation, or parser error.

## Comparison validity

A Run is comparison-eligible only when:

- Nginx exposes the packaged dynamic reverse proxy with HTTP/2 support;
- every h2load job reports HTTP/2, zero failed requests, and zero network-level
  errors;
- every per-request log exactly matches its fixed request count;
- every Generator process/host CPU observation exists; peak h2load process CPU
  normalized by the job's declared native-thread count remains below 90% of
  that capacity, and peak host CPU remains below 90%;
- every Generator request-log cleanup is verified; and
- Target application, Nginx, certificate, key, payload, and workspace cleanup
  is verified.

Provider observations expose the fixed C8/M16 request rate, P99, and success
rate under the exact profile, methodology, topology, and evidence-class
contract. They remain descriptive and do not create a provider rating or an
uncalibrated workload threshold.

## Safety and limitations

This profile is controlled load testing against an operator-owned paired
Target, not DDoS testing. The Target address comes from the authenticated
pairing session, Nginx permits only the paired Generator, and CloudMark never
accepts a public arbitrary URL. Only one saturation/service suite may use an
Agent at a time.

The self-signed certificate measures TLS/HTTP2 processing but not public trust.
Version 1 does not measure browser rendering, HTTP/3, QUIC, WebSocket, uploads,
database-backed requests, CDN, WAF, autoscaling, public certificate issuance,
geographic end-user latency, or denial-of-service resilience.
