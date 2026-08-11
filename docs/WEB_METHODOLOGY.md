# Web, API, and TLS methodology

CloudMark methodology `web-http-v1` measures a controlled HTTP/TLS service
from a separate request generator. The Controller coordinates the run and
stores evidence; it never carries benchmark traffic.

## Topology and tools

- **Target Agent:** creates an isolated Nginx service beneath its configured
  Agent workspace and listens on its exact advertised provider address.
- **Generator Agent:** runs ApacheBench against only the Target address and
  the fixed CloudMark endpoints.
- **Controller:** validates the paired session, dispatches allow-listed tasks,
  records progress and evidence, and coordinates verified cleanup.

The Target reports Nginx and OpenSSL versions. The Generator reports the
ApacheBench version. A run is rejected when a role is missing its required
tool.

## Fixed service contract

The executor creates three immutable payloads for each run:

| Endpoint | Body | Purpose |
|---|---:|---|
| `/health` | 3 bytes | connection and TLS setup behavior |
| `/api/v1/record` | 1,024 bytes of valid JSON | API-sized response serving |
| `/assets/256k.bin` | 262,144 bytes | static transfer throughput |

HTTP uses TCP 58080 and HTTPS uses TCP 58443. Nginx binds only the Target's
advertised address. Ordered access rules allow the paired Generator and the
Target itself, then deny every other client. The API cannot supply a URL,
path, port, payload, Nginx directive, or command fragment.

The HTTPS listener uses a one-day, per-run self-signed RSA-2048 certificate
whose subject alternative name is the Target IP. `web-http-v1` fixes TLS 1.2
and `ECDHE-RSA-AES128-GCM-SHA256`, disables TLS session caching and tickets,
and records the protocol and cipher reported by ApacheBench. This measures TLS
handling cost; it does not evaluate public certificate issuance or trust-chain
quality.

## Profiles

`web-peer-quick` provides a short baseline across single-client HTTP, HTTP and
HTTPS concurrency, new TLS connections, and 256 KiB HTTP transfer.

`web-peer-standard` adds HTTP and HTTPS concurrency curves up to 64 clients,
longer measurement windows, and HTTP/HTTPS static-transfer comparisons. Every
job has a warm-up window followed by a bounded measured window. Concurrency is
restricted to 1, 4, 8, 16, or 64 and one job may run for at most 60 seconds.
The Generator also enforces a high request-count ceiling so the time limit,
rather than an unbounded request loop, defines the workload.

## Recorded evidence

Each measurement preserves:

- complete, successful, failed, and non-2xx request counts;
- requests per second and average request time;
- P50, P90, P95, P99, and maximum latency;
- transfer rate and transferred-byte counts;
- connection timing statistics and failure breakdown when reported;
- keep-alive request count;
- TLS protocol/cipher evidence for HTTPS; and
- raw ApacheBench stdout/stderr plus tool identity.

The result also records target/generator identity, profile and methodology
versions, fixed ports, service configuration evidence, and cleanup status.

## Interpretation limits

ApacheBench is a single-process generator and can become the bottleneck before
the Target does. CloudMark therefore reports measurements as evidence, keeps
the Web/API/TLS domain `Partial`, and does not infer provider-wide serving
capacity from one pair or one time window. A production comparison should use
equivalent Generator instances and confirm that Generator CPU/network headroom
does not invalidate the result.

`web-http-v1` does not measure a dynamic application runtime, upstream reverse
proxy, database dependency, HTTP/2, HTTP/3, CDN, WAF, autoscaling, global load
balancing, public certificate operations, or DDoS resilience. Those require
separate, explicitly versioned evidence.

## Safety and cleanup

- `confirm_web_load=true` is required.
- Both Agents must be authenticated members of the selected ready session.
- The Target Agent must run as a non-root account.
- Only one paired service or saturation suite may use a Target at a time.
- The workspace retains at least 512 MiB or 5% free space, whichever is larger.
- A Target watchdog stops Nginx and removes the service directory after
  success, failure, timeout, cancellation, or loss of Controller contact.
- Unknown residual service directories are never overwritten or deleted
  automatically after an abrupt Agent or host failure.
- CloudMark does not provide an arbitrary target or DDoS mode.

Comparisons must match profile version, methodology version, target identity,
Generator class, tool versions, topology, operating system, and time window.
