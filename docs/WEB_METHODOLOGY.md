# Web, API, and TLS methodology

CloudMark `web-http-v2` extends the controlled HTTP/TLS service with a bundled
dynamic application behind Nginx, Linux Generator process-CPU evidence, and a
fixed HTTP/2 negotiation observation. `web-http-v1` Quick results remain
readable under their original static-service contract. The Controller
coordinates the run and stores evidence; it never carries benchmark traffic.

## Topology and tools

- **Target Agent:** creates an isolated Nginx service beneath its configured
  Agent workspace. Web v2 also starts the packaged Python application fixture
  on fixed loopback TCP port 58081 and reverse-proxies its dynamic endpoint.
- **Generator Agent:** runs ApacheBench against only the Target address and
  fixed CloudMark endpoints, records bounded Linux procfs CPU intervals for the
  ApacheBench process, and uses HTTP/2-capable curl for one protocol observation.
- **Controller:** validates the paired session, dispatches allow-listed tasks,
  records progress and evidence, and coordinates verified cleanup.

The Target reports Nginx, OpenSSL, and packaged Python runtime evidence. The
Generator reports ApacheBench and curl versions. Web v2 is rejected before load
unless Nginx and curl explicitly report HTTP/2 support and the Generator exposes
Linux procfs process accounting.

## Fixed service contract

The executor creates three immutable static payloads and one Web v2 application
response contract for each run:

| Endpoint | Body | Purpose |
|---|---:|---|
| `/health` | 3 bytes | connection and TLS setup behavior |
| `/api/v1/record` | 1,024 bytes of valid JSON | API-sized response serving |
| `/api/v2/dynamic` | 1,024 bytes of valid JSON | Python application plus Nginx reverse-proxy path |
| `/assets/256k.bin` | 262,144 bytes | static transfer throughput |

HTTP uses TCP 58080 and HTTPS uses TCP 58443. Nginx binds only the Target's
advertised address. Ordered access rules allow the paired Generator and the
Target itself, then deny every other client. The API cannot supply a URL,
path, port, payload, Nginx directive, or command fragment.
The Python fixture accepts only `/ready` and `/api/v2/dynamic`, binds only
`127.0.0.1:58081`, and rebuilds a deterministic JSON response for every dynamic
request. The application port is never exposed to the Generator.

The HTTPS listener uses a one-day, per-run self-signed RSA-2048 certificate
whose subject alternative name is the Target IP. `web-http-v1` fixes TLS 1.2
and `ECDHE-RSA-AES128-GCM-SHA256`, disables TLS session caching and tickets,
and records the protocol and cipher reported by ApacheBench. This measures TLS
handling cost; it does not evaluate public certificate issuance or trust-chain
quality. Web v2 enables HTTP/2 on that listener and makes one fixed curl request
to record the actually negotiated protocol, response code, and connection/TLS/
TTFB/total timing. That single request is protocol evidence, not HTTP/2 load or
capacity evidence.

## Profiles

`web-peer-quick` provides a short baseline across single-client HTTP, HTTP and
HTTPS concurrency, new TLS connections, and 256 KiB HTTP transfer.

`web-peer-standard` version 2 adds HTTP and HTTPS concurrency curves up to 64
clients, longer measurement windows, HTTP/HTTPS static-transfer comparisons,
three dynamic reverse-proxy workloads at concurrency 1/16/64, and the fixed
HTTP/2 observation. Every ApacheBench job has a warm-up window followed by a
bounded measured window. Concurrency is
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
- one-second Generator host CPU, steal-time, and ApacheBench process CPU
  summaries when Linux procfs exposes them; and
- raw ApacheBench stdout/stderr plus tool identity.

The result also records target/generator identity, profile and methodology
versions, fixed ports, dynamic application/reverse-proxy identity, HTTP/2
negotiation, service configuration evidence, and cleanup status.

## Interpretation limits

ApacheBench is a single-process generator and can become the bottleneck before
the Target does. Web v2 marks a Standard Run comparison-ineligible when any
measured job lacks Generator CPU evidence or the ApacheBench process reaches
90% of one logical CPU. It separately reports aggregate Generator host CPU and
steal time. This closes the known load-generator validity gap without treating
Target saturation as an error.

`web-http-v2` measures only its packaged Python application and an HTTP/1.1
ApacheBench load path through Nginx. It does not measure HTTP/2 load, a database
dependency, HTTP/3, CDN, WAF, autoscaling, global load balancing, public
certificate operations, or DDoS resilience. Those require separate, explicitly
versioned evidence.

## Safety and cleanup

- `confirm_web_load=true` is required.
- Both Agents must be authenticated members of the selected ready session.
- The Target Agent must run as a non-root account.
- Only one paired service or saturation suite may use a Target at a time.
- The workspace retains at least 512 MiB or 5% free space, whichever is larger.
- A Target watchdog stops Nginx and the bundled application process, then
  removes the service directory after
  success, failure, timeout, cancellation, or loss of Controller contact.
- Unknown residual service directories are never overwritten or deleted
  automatically after an abrupt Agent or host failure.
- CloudMark does not provide an arbitrary target or DDoS mode.

Comparisons must match profile version, methodology version, target identity,
Generator class, tool versions, topology, operating system, and time window.
