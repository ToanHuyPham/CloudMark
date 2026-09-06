# Linux security-posture evidence foundation

`linux-security-posture-v2` is a read-only evidence collector foundation for a
future CloudMark Security executor. It is intentionally not registered as an
Available benchmark profile yet and produces no security score.

## Fixed evidence scope

The collector reads only a fixed set of Linux procfs, sysfs, securityfs, and
EFI-variable paths:

- address-space layout randomization;
- Yama ptrace scope;
- protected hard links, symbolic links, FIFOs, and regular files;
- unprivileged BPF policy;
- kernel-pointer, dmesg, and performance-event restrictions;
- kernel-module loading, kexec loading, user-namespace, minimum mmap address,
  SUID core-dump, and Magic SysRq controls;
- TCP SYN cookies, IPv4/IPv6 redirect handling, and IPv4 reverse-path filter;
- active Linux Security Modules;
- AppArmor and SELinux enforcement visibility;
- kernel lockdown mode;
- FIPS mode visibility;
- cgroup v2 controller visibility;
- core-dump destination class;
- UEFI Secure Boot state when exposed to the guest; and
- exact `/tmp`, `/var/tmp`, `/dev/shm`, `/home`, `/boot`, and `/boot/efi`
  mount hardening visibility.

Each control read is capped at 4,096 bytes. Missing, malformed, oversized, or
unsupported values remain `unavailable`; they never become zero or a failed
security grade. Symbolic-link control paths are refused.

## Redaction and trust boundary

The raw kernel core pattern is never retained. CloudMark stores only whether it
uses a pipe handler or a filesystem pattern, preventing handler paths or
arguments from entering evidence. For Secure Boot, the EFI variable filename
and GUID are not retained; only the boolean state and generic source pattern
are exposed.

Mount evidence reads at most 1 MiB and 4,096 rows from
`/proc/self/mountinfo`. It retains only an exact system mountpoint, recognized
filesystem type, and the `ro`, `nodev`, `nosuid`, and `noexec` booleans. Source
devices, non-target mountpoints, and raw option strings are never persisted.
An unavailable exact mountpoint means it is not independently mounted in the
guest; it is not automatically a failed security control.

LSM and cgroup controller names are bounded identifiers rather than secrets.
The collector executes no subprocess, performs no network request, opens no
credential store, and writes no operating-system configuration.

## Interpretation limits

Guest-visible controls do not prove physical-host configuration, provider
control-plane security, tenant isolation, patch quality, vulnerability status,
firewall policy, SSH policy, disk encryption, IAM/RBAC, compliance, audit-log
retention, incident response, or vulnerability-management maturity. Secure
Boot or lockdown may be hidden by a hypervisor even when the provider secures
the physical host.

The collector becomes a production executor only after Controller/Agent
integration, explicit evidence versioning in Runs, dashboard presentation,
platform-specific capability reporting, and provider-comparison rules are
implemented and verified. Until then it remains a tested internal foundation,
not completed Security-domain coverage.
