# Network methodology

CloudMark does not benchmark from provider machines back to the operator's
controller. The supported provider network model is direct agent-to-agent data
traffic.

## Two-agent topology

- Agent A: target/server.
- Agent B: generator/client.
- Controller: session orchestration and result storage only.

The standard profile records A→B, B→A, and bidirectional results independently.
It never averages directions before storing the raw measurements.

## Planned measurements

- TCP with 1, 4, 8, and 16 streams;
- UDP rate sweep with loss, jitter, and reorder;
- idle and loaded latency;
- retransmission and sender/receiver CPU;
- MTU and route evidence;
- generator saturation detection;
- short burst and sustained bandwidth.

## Validity

Two VMs on the same physical host are valid for virtual-switch and hypervisor
tests, not for provider fabric claims. Provider-grade results should use
anti-affinity or placement evidence and repeat across fresh instances and time
windows.

Peer execution is not enabled in `0.1.0-alpha`; pairing, roles, persistence, and
direction policy are implemented so the executor can be added without changing
the API contract.
