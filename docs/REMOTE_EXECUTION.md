# Remote Agent execution

CloudMark version `0.5.0` allows the local Controller to dispatch compute,
memory, and storage profiles to a selected authenticated Agent. Benchmark load
and temporary storage files remain on the provider machine; only control events
and evidence travel to the Controller.

## Topology

```text
Operator machine
└── Controller + dashboard + SQLite evidence store

Provider environment
├── Agent A — inventory, CPU, memory, and storage target
└── Agent B — second target and direct network peer
```

Selecting an Agent is explicit. Omitting `agent_id` from an API request executes
the suite on the Controller host. CloudMark records `execution.mode`, Agent ID,
Agent name, role, session ID, Agent version, target inventory, and provider
evidence in every completed remote result.

## Start an Agent

Create a pairing session in **Distributed Testing**, then bootstrap the required
packs on the provider VM:

```bash
sudo python -m cloudmark bootstrap --packs compute,memory,storage,network --yes
```

Start the persistent worker:

```bash
cloudmark agent \
  --controller https://CONTROLLER \
  --session SESSION_ID \
  --token JOIN_TOKEN \
  --role target \
  --advertise-address VM_PRIVATE_IP \
  --workspace /var/lib/cloudmark/benchmark-workspace
```

Use a dedicated filesystem path when storage placement matters. The Agent never
accepts a workspace path from a remote task. The path is configured locally by
the operator, preventing a Controller request from selecting an arbitrary file
target.

Remote HTTP is rejected by default. `--allow-http` is acceptable only on an
isolated private management network or trusted VPN; it must not be used across
the public Internet.

## Run from the dashboard

1. Keep the Agent process running.
2. Open **Compute & Memory** or **Storage Assessment**.
3. Select the exact Agent under **Execution target**.
4. Confirm the OS and CPU/Memory/Storage capability indicators.
5. Start one profile and leave the target otherwise idle.
6. Watch task heartbeat, job name, progress, and partial evidence.
7. Use **Cancel run** when required. The Agent polls cancellation while a child
   benchmark process is active and terminates that process through the shared
   runner boundary.

## Run through the API

```json
{
  "suite": "compute",
  "profile": "compute-quick",
  "agent_id": "agent_123",
  "confirm_load": true
}
```

Storage uses `confirm_write: true`. Memory uses `confirm_load: true` and requires
a Linux Agent with GCC/OpenMP. The selected Agent must advertise the required
tool during inventory enrollment.

## Control and safety contract

- Remote kinds are fixed to `benchmark-compute`, `benchmark-memory`, and
  `benchmark-storage`; arbitrary commands and shell fragments are refused.
- Suite, profile, protocol version, confirmation, and timeout are checked again
  by the Agent before execution.
- Profile job arguments come only from the installed CloudMark profile catalog.
- A target can run only one compute, memory, or storage saturation task at a
  time. A network assessment cannot overlap with a remote suite in the same
  session.
- The Agent publishes a task heartbeat every second while child work is active.
- If Controller contact is unavailable for more than 20 seconds, the Agent
  cancels the benchmark rather than continuing uncontrolled load.
- The Controller treats a task heartbeat gap longer than 45 seconds as a failed
  remote execution and closes the task.
- Controller restart cancels unfinished runs and tasks. A surviving Agent sees
  the terminal task state and stops its child process.
- Request bodies are bounded at 16 MiB to accommodate raw time-series evidence
  without accepting unbounded uploads.

## Result validation

The Controller accepts a completed result only when its suite, profile,
profile version, methodology version, and remote protocol version match the
dispatched task. Tool and compiler versions remain part of the result. A failed
or cancelled task preserves the most recently acknowledged partial evidence but
never becomes a completed assessment.

## Current boundary

Version `0.5.0` does not remotely install packages or execute provider
control-plane mutations. Bootstrap remains an explicit operator action requiring
root or administrator approval. Automatic Agent re-enrollment, fleet labels,
scheduled repetitions, and cross-instance aggregation are following milestones.
