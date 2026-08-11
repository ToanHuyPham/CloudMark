# CloudMark repository guidance

## Product intent

CloudMark is an evidence-driven infrastructure assessment platform for cloud
instances, VPS products, bare-metal servers, and self-hosted public-cloud
environments. Treat it as production software for repeatable provider
evaluation, not as a lab demo or a collection of synthetic scores.

The project must remain useful for global providers and smaller Vietnamese
providers. Do not assume that a metadata endpoint, managed service, or provider
API exists. Preserve a clear distinction between detected facts, operator
claims, measured evidence, inference, and unavailable evidence.

## Durable context

Read these files before making architectural or operational changes:

- `docs/PROJECT_HANDOFF.md`
- `docs/CURRENT_STATE.md`
- `docs/ARCHITECTURE.md`
- `docs/SAFETY.md`
- `docs/OPERATIONS_RUNBOOK.md`
- `docs/DECISIONS.md`

Update `docs/CURRENT_STATE.md` when a milestone, verified environment, known
limitation, or next priority materially changes. Update the runbook when a
command, port, runtime path, credential flow, or recovery procedure changes.

## Language and presentation

- Repository content, source code, UI copy, commit messages, and public
  documentation must be written in English.
- User-facing benchmark claims must identify the workload, profile version,
  methodology version, target identity, and evidence status.
- Never present `Partial` or `Roadmap` capabilities as completed or assign them
  artificial zero scores.

## Safety boundaries

- Never commit `.cloudmark/`, `.env*`, private keys, Controller tokens, join
  tokens, public IP addresses tied to an active test environment, or provider
  credentials.
- Storage benchmarks must use filesystem test files, preserve the configured
  free-space reserve, and clean up after success, failure, timeout, or cancel.
- Never target a raw block device, format a volume, issue discard/TRIM, or run a
  destructive preconditioning pass without a separately reviewed design and
  explicit operator authorization.
- Do not start CPU, memory, storage, web-load, database-load, or network-load
  benchmarks merely to validate a code change. Use unit tests and preflight
  paths unless the operator explicitly authorizes a benchmark target and run.
- Provider network measurements must flow between paired provider Agents. The
  Controller is coordination and evidence storage, not a throughput endpoint.
- Keep only one saturation suite active per target unless the test explicitly
  measures contention.

## Development setup

Requirements: Python 3.9 or newer, Node.js 22 or newer, and pnpm 11.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
pnpm install
```

Start the local stack with:

```powershell
.\scripts\start-local.ps1
```

Stop only processes recorded by that launcher with:

```powershell
.\scripts\stop-local.ps1
```

Manual commands remain documented in `docs/USER_GUIDE.md`.

## Verification

Run the smallest relevant checks first. Before handing off a material change,
run the complete non-load-bearing suite:

```powershell
python -m unittest discover -s tests_python -v
pnpm test
```

Also run `pnpm run lint` for dashboard changes. Do not run full benchmarks in
shared CI or on a developer workstation as part of routine verification.

## Runtime data and recovery

`.cloudmark/` is local runtime state and is intentionally ignored by Git. Use
`scripts/backup-runtime.ps1` and `scripts/restore-runtime.ps1`; never solve
portability by removing `.cloudmark/` from `.gitignore`.

Before transferring the project to another Codex installation, verify that:

1. all intended source changes are committed and pushed;
2. a runtime snapshot exists outside the repository;
3. secret-bearing snapshots are encrypted at rest;
4. `docs/CURRENT_STATE.md` reflects the latest verified state; and
5. no live credential or active provider address appears in tracked files.

## Git discipline

- Preserve unrelated user changes in a dirty worktree.
- Keep generated dependencies, build output, runtime state, and secrets out of
  commits.
- Use focused commits and report the verification performed.
- Do not rewrite history or use destructive Git recovery commands unless the
  operator explicitly requests them.
