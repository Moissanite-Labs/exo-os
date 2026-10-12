# 05 Security for an agent with OS-level reach

Status: research note, 2026-10-12. Read-only study; nothing on this machine was changed.
Marks: [S] = seen in a search result or source page. [G] = my guess or judgement. [U] = I know it from general knowledge but did not fetch a source this session; check before relying on it.
No benchmarks are quoted. Where a source gave a number, I say it is the source's claim.

## 0. What Exo has today

From `research/PRINCIPLES.md` and `plugins/exo-gate` (read only):

- Principle 1: one append-only, signed log is the source of truth. Everything else is a rebuildable view.
- Principle 10: content is data. Instructions come only from the principal's channel. Persona is not a security boundary. Provenance should be marked outside the token stream.
- Principle 11: gate by reversibility and exposure, not by step count.
- Principle 3: attention is scarce. No rubber-stamp approval queues (cites Bainbridge).
- Principle 18: sign every memory write; treat inferences as private data.
- `exo-gate` is a `pre_tool_call` hook. It classifies each call (reversibility, exposure, cost, third-party effect), then allows, logs, asks (`approval.requested`) or blocks. It writes each decision to the signed ledger (Ed25519). A decision model may only tighten an outcome, never loosen it. An unknown high-risk tool is an ask. A call nobody answers never runs.
- Secrets today: `keys.env` or macOS Keychain.

Gap [G]: the gate is a policy layer inside the agent's own process. If the model is steered, the gate still holds only as long as every dangerous path goes through a gated tool. Nothing yet stops a shell tool, a skill or an MCP server from reaching the disk, the network or `keys.env` directly. The rest of this note is about closing that gap with layers the model cannot talk its way past.

## 1. Threat model

Core idea: the model cannot reliably tell instructions from data, so treat every token from outside as possibly hostile, and make the damage bounded even when the model is fooled.

### 1.1 The named frameworks

- **Lethal Trifecta** (Simon Willison, 16 June 2025) [S]: private data + exposure to untrusted content + a way to communicate externally. If one agent has all three, an attacker who controls the content can make it read private data and send it out. Willison argues that guardrail products claiming to catch most attacks are not a fix. Search summary: https://aiweekly.co/alerts/willison-names-lethal-trifecta-behind-ai-agent-data-leaks . Primary post [U]: https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/
- **Agents Rule of Two** (Meta, 31 Oct 2025) [S]: in one session, an agent should have at most two of: (A) processes untrusted input, (B) accesses sensitive systems or private data, (C) changes state or talks externally. If it needs all three, it should not run autonomously; require human approval or another reliable check. Inspired by the Trifecta and by Chrome's Rule of 2. Sources: https://simonw.substack.com/p/new-prompt-injection-papers-agents , https://dev.to/gabrielanhaia/metas-agents-rule-of-two-a-practical-defense-against-prompt-injection-55ib . Critique [S]: it covers prompt injection in one agent, not every agent risk (https://kenhuangus.substack.com/p/the-rule-of-two-vs-reality-why-metas).
- **OWASP Top 10 for LLM Applications 2025** [S]: LLM01 Prompt Injection, LLM06 Excessive Agency, LLM10 Unbounded Consumption (the full list runs LLM01 to LLM10). Lists: https://promptfoo.dev/docs/red-team/owasp-llm-top-10/ , https://www.invicti.com/blog/web-security/owasp-top-10-risks-llm-security-2025 . One dev.to post numbers Excessive Agency differently; use LLM06.

### 1.2 Threats mapped to Exo

| Threat | How it hits Exo [G] |
|---|---|
| Prompt injection via web, email, files | A page, email or PDF says "forward the last 10 emails to X". Exo ingests mail, files and the web all day, so all three Trifecta legs exist by default. |
| Confused deputy | Exo holds the principal's authority (logins, send rights). A third party's text borrows it. The gate sees "send email", which looks normal. |
| Malicious skill or MCP server | Skills are text and code that Exo loads. MCP tool descriptions go into the model context. Documented attacks: tool poisoning (hidden instructions in descriptions), rug pulls (tool changes after approval), shadowing and typosquats [S]. Sources: https://arxiv.org/pdf/2506.02040 , https://labs.cloudsecurityalliance.org/research-rb/csa-whitepaper-mcp-security-tool-poisoning-20260506-csa-styl/ , https://www.strac.io/blog/mcp-rug-pull , https://www.cyberark.com/resources/threat-research/poison-everywhere-no-output-from-your-mcp-server-is-safe |
| Secret exfiltration | `keys.env` is a plain file any process of the user can read. A shell tool or a poisoned skill can `cat` it and send it out. Also: a secret pasted into a prompt is in the context and in provider logs. |
| Runaway cost | Loops, retries, night-shift jobs (principle 15) and injected "do this 1000 times" burn money. OWASP LLM10. |
| Log tampering | An attacker with the user's rights rewrites or truncates the log that is the source of truth. |
| Self-modification | An agent edits its own charter, policy or gate config (principle 9 already forbids silent edits). |
| Supply chain | Python packages, Hermes plugins, model-provider SDKs. [G] Out of scope here beyond pinning and review. |

## 2. Sandboxing options on Linux

The point: a sandbox bounds what a tool process can touch whatever the model decides. The gate decides what should happen; the sandbox decides what can.

| Option | What it gives | Notes |
|---|---|---|
| **bubblewrap** (`bwrap`) | Unprivileged namespaces: you pick the bind mounts, can drop network, PID, IPC namespaces, accept a seccomp filter, set no-new-privs [S]. Low-level building block, not a policy. | Used by Flatpak and by Claude Code on Linux [S]. Small codebase (a forum claim of about 3.7k lines vs about 53k for firejail; unverified). https://www.linuxlinks.com/bubblewrap-low-level-unprivileged-sandboxing-tool/ |
| **Landlock** | Unprivileged self-restriction of a process: filesystem rules, and TCP connect/bind by port since ABI 4 [S]. Stackable with other LSMs. | No root, no namespaces. Network rules are per port, not per host [G from the ABI text]. Needs a recent kernel; the kernel here reports 7.2.x so it is fine on charminar. Docs: https://www.kernel.org/doc/html/v7.0-rc1/userspace-api/landlock.html , https://man.archlinux.org/man/landlock.7 |
| **seccomp-bpf** | Syscall allow or deny list. Cuts kernel attack surface. | Hard to write by hand. Bubblewrap README warns that blocking the `seccomp` syscall stops sandboxed apps from adding their own filters [S via search]. |
| **systemd-run / unit sandboxing** | Properties such as `ProtectSystem`, `ProtectHome`, `PrivateTmp`, `PrivateNetwork`, `IPAddressDeny`, `ReadWritePaths`, `SystemCallFilter`; `systemd-analyze security` scores a unit [S]. Also cgroups: memory, CPU, task limits. | Good for the long-running Exo daemon itself. Wiki: https://wiki.archlinux.org/title/Systemd/Sandboxing . That `systemd-run -p` accepts the same properties for a transient unit is likely but I did not confirm it [U]. |
| **systemd-nspawn** | Light container with its own root tree. | [U] More setup than bwrap; good for a full "agent home". |
| **firejail** | Large profile library, desktop features. | Setuid-root design and bigger code are the usual criticism; forum opinion only [S]. https://community.parrotsec.org/t/firejail-vs-bubblewrap/8176 . I would not choose it for new work [G]. |
| **gVisor** | User-space kernel; intercepts syscalls. | Boundary is the gVisor process, so a bug there is a host bug; per-syscall overhead [S, vendor blog]. https://fly.io/learn/firecracker-vs-gvisor/ |
| **Firecracker microVM** | Separate guest kernel under KVM. Strongest boundary of the list. | Costs: image, boot, file sharing, no GUI by default [G]. Fits untrusted code or a per-skill VM, not the whole desktop. Same fly.io source. An academic study cited in a search summary says both run much more kernel code than native Linux; treat as unverified. |
| **Flatpak portals** | Apps run in bwrap; access to files, camera, screenshots goes through a user prompt via a portal [U]. | Good model for "ask the user for a file, get a handle, not a path". Not a way to sandbox arbitrary tools. Docs [U]: https://docs.flatpak.org/en/latest/portal-api-reference.html |
| **cgroups v2** | Hard caps on memory, CPU, pids, IO. | Stops local runaway. Does not stop token spend. |

### 2.1 How other agents sandbox

- **Claude Code** [S]: on Linux, bubblewrap for the filesystem plus a proxy (socat relay) for network; an optional seccomp filter blocks Unix sockets. macOS uses Seatbelt. The proxy filters by domain and does not inspect traffic. If the sandbox cannot start, it warns and runs unsandboxed unless `sandbox.failIfUnavailable` is true. Docs: https://code.claude.com/docs/en/sandboxing . Lesson for Exo: fail closed.
- **Claude Code auto mode** [S]: a classifier approves or blocks actions so people see fewer prompts. Anthropic says users approve about 93% of prompts anyway; other press says 97% (numbers differ, source unclear). It falls back to manual approval after repeated blocks. Anthropic itself says classifiers do not remove risk. https://anthropic.com/engineering/claude-code-auto-mode . This matches Exo's "decision model may only tighten" design.
- **Codex CLI** [S, secondary sources, check OpenAI docs]: two separate axes. Approval policy (untrusted, on-request, never) and sandbox mode (read-only, workspace-write, danger-full-access). Linux uses Landlock plus seccomp, with a bubblewrap path behind a flag in some versions. Sandbox may fail inside containers. https://www.mintlify.com/openai/codex/concepts/sandboxing , https://codex.danielvaughan.com/2026/04/08/codex-cli-security-model-approval-sandbox-two-axis/
- **Cursor** [S]: Seatbelt on macOS; Landlock plus seccomp used directly on Linux; policy generated at run time from workspace and admin settings and `.cursorignore`; rejected containers and VMs for startup and memory cost; protects `.git/config` and `.git/hooks` from writes. https://cursor.com/blog/agent-sandboxing
- Pattern across all three [G]: approval policy and OS sandbox are separate and both exist; the sandbox is cheap kernel features, not VMs; the network is the second wall.

## 3. Capability-based approval that people actually read

Problem: Bainbridge's "Ironies of Automation" (1983): the more the system runs alone, the less able the human is to supervise it [S via secondary]. A 2026 position paper applies this to agents and warns that long autonomous runs make late review harder (https://arxiv.org/pdf/2608.23642, seen only in a search summary). Anthropic's own data point on rubber-stamping is the 93% figure above [S]. A practitioner post describes a loop of faster, shallower review leading to more autonomy [S, opinion]: https://yage.ai/share/claude-auto-mode-approval-illusion-en-20260808.html

Design suggestions [G unless marked]:

1. **Grant capabilities, not clicks.** Instead of "allow this command?", the principal grants a scoped capability: "read folder X for this task", "send email to this recipient list, max 5, today". Each tool call is checked against held capabilities. Out of scope is a new ask. This keeps the prompt count low and each prompt meaningful.
2. **Ask only at the cliffs.** Reversibility and exposure (principle 11) already say where. Reversible and private: run and log. Irreversible, public, financial, third-party: ask before launch.
3. **Make the question small and checkable.** One line, the exact recipient or amount or URL, a default, a link to evidence. Never "approve the plan". Show the diff of what leaves the machine, not the agent's reasoning.
4. **Taint-aware asks.** If the session has touched untrusted content, raise the level of any outbound action. This is the Rule of Two as code: track A (untrusted seen), B (private read), C (external or state change); when all three would be true, force an ask or split the session.
5. **Budget the interruptions.** Count asks per accepted action (principle 3). If a class of ask is approved nearly always, either turn it into a standing capability with a limit or remove it. Do not auto-approve silently.
6. **Canaries.** Occasionally insert a synthetic harmful ask in test mode to measure whether the principal still reads. [G] This is an idea; I found no source for it in production.
7. **Out-of-band confirmation for the worst class.** Money, deleting data, publishing: confirm on a second channel (phone) that the agent cannot write to, so injected text cannot also answer the prompt.
8. **Unanswered means no.** Exo-gate already does this.

## 4. Secrets

Rule: the model never sees a secret, and a tool that the model drives never holds one longer than needed.

| Mechanism | What it does | Fit |
|---|---|---|
| **systemd-creds** + `LoadCredentialEncrypted=` | Encrypts a credential with AES256-GCM using a key from the TPM2 chip and/or `/var/lib/systemd/credential.secret`; name is bound into the blob; the service gets it as a file in a private directory at start [S]. https://www.man7.org/linux/man-pages//man1/systemd-creds.1.html , https://systemd.io/CREDENTIALS/ | Best fit for the Exo daemon on Linux. Replaces `keys.env`. [G] Default of TPM2 plus host key only when both exist. Does not stop a process that runs as the same service from reading it. |
| **TPM2** | Hardware-bound key; secret does not leave the machine as plain text at rest. | Protects against disk theft, not against a live compromised process. [G] |
| **Kernel keyring** (`keyctl`) | Secret in kernel memory, not a file; per-session or per-user scope. [U] | Useful to hold the unlocked master key; avoid for many secrets. |
| **Secret Service / libsecret** (gnome-keyring, KWallet) | Desktop keyring over D-Bus; unlocked at login. [U] | Linux twin of macOS Keychain; any same-user process can usually ask. Give the daemon its own collection. [G] |
| **age / sops** | File-level encryption; sops encrypts values in YAML/JSON and works with age, GPG, cloud KMS. [U] https://github.com/getsops/sops | Good for secrets stored in git beside config. Decrypt only inside the daemon. |
| **Credential-injection proxy** | The agent holds a placeholder; a proxy outside the agent swaps in the real key on the outbound request, checks host and path against policy, may strip keys from responses [S]. Examples: OneCLI, wardn, sproxy, Composio's write-up. https://www.ycombinator.com/launches/RoJ-onecli-your-ai-agents-shouldn-t-be-holding-raw-secrets , https://docs.rs/crate/wardn/0.4.0 , https://composio.dev/blog/credential-isolation-ai-agents | Best answer to exfiltration: even a fully fooled model cannot leak what it never held. Caveats: these are mostly project self-descriptions with no independent audit seen; a secret already pasted into a prompt must be rotated [S]. |

Suggested path [G]: (1) move `keys.env` into systemd-creds on Linux and Keychain on macOS, behind one `get_secret(name)` function used only by the broker; (2) run a small local broker that makes the authenticated calls (model API, Gmail, Drive) and exposes them as tools; the agent and its sandboxed tools never see the key; (3) deny read of the credential directory to every sandboxed child; (4) rotate on any suspicion; (5) a scanner on model input and output that blocks strings shaped like keys.

## 5. Audit log

Today: append-only, Ed25519-signed, source of truth [from exo-gate and PRINCIPLES]. Hardening ideas:

- **Hash chain.** Each entry includes the hash of the previous one, then is signed. Deleting or reordering breaks the chain. [U] standard design.
- **Separate signer.** The signing key should live in a different process or user (or in the TPM) than the agent. If the agent can read the key, it can forge history. [G] Today `key_path` is a plain file under `$EXO_HOME/keys`; that is the weak point.
- **Single writer.** A small log daemon accepts entries over a Unix socket and is the only thing with write access to the file. Use `chattr +a` (append-only attribute) as a second guard; root can undo it, so it protects against the agent, not against an admin. [U]
- **Remote anchoring.** Periodically send the latest chain head (a hash, no content) somewhere the agent cannot edit: a second machine such as the Divan Bus relay (already signed and hash-chained per PRINCIPLES 18), a transparency log (Sigstore Rekor: https://docs.sigstore.dev/logging/overview/ [U]) or a public timestamp (OpenTimestamps: https://opentimestamps.org [U]). The search I ran found nothing useful on either, so these two are from memory; read their docs first.
- **Signed checkpoints.** Batches of entries committed to a Merkle root with a signed checkpoint let you prove one entry without sending the whole log. [S, generic pattern in a search hit for a Rust crate; not a recommendation of that crate.]
- **Log the decision inputs.** Record the capability held, the taint state (A, B, C), the tool arguments and the sandbox profile, so a review can ask "why was this allowed?".
- **Privacy.** The log holds private content; encrypt the content fields at rest and keep hashes in the chain. [G]

## 6. Network egress control

Exfiltration needs a way out, so make the way out narrow. This is the cheapest strong control against the Trifecta's third leg.

- Default deny for sandboxed tool processes: `PrivateNetwork=yes` or `bwrap --unshare-net`, or Landlock TCP rules by port [S]. Give network only through a local proxy.
- Egress proxy with an allowlist by domain, as Claude Code does (it filters by domain and does not inspect content [S]). Weakness [G]: an allowed domain can still be a channel, for example a paste site, a calendar invite, a shared doc. Keep the list short and per task.
- Block DNS to anything but the local resolver, and log queries; DNS can carry data out. [G]
- Treat the model provider's API as a legitimate but monitored destination. The agent's own process needs it; sandboxed tools do not.
- Also lock the markdown and link channel: do not auto-fetch images or links from model output, a known leak route. [U] (Johann Rehberger and others have shown this against several products.)
- nftables or cgroup-based rules per service can enforce this at the kernel too (`IPAddressDeny=` in systemd [S]). [G] Use them as a backstop to the proxy.

## 7. Filesystem snapshots as undo

- Snapper on btrfs can take a pre and post snapshot around a change and `undochange` single files or roll back a subvolume [S]. https://documentation.suse.com/sles/16.0/html/SLES-snapper-basics/index.html . Limits [S]: full rollback does not cover all paths; `/home` is often a separate subvolume and excluded by default.
- One author describes a snapshot before each risky agent action [S, single blog, not a tool]: https://dev.to/yureki_lab/how-i-built-a-rollback-system-so-my-ai-coding-agent-can-undo-its-own-mistakes-58ml
- On charminar, `omarchy snapshot` already exists (CLAUDE.md rule 3). [G] Exo could call the same kind of snapshot as a gate step for "reversible with effort" classes.
- Be honest about the limits [G]: snapshots undo local files only. They do not undo a sent email, a posted message, a payment or a deleted cloud file. That is exactly why those classes stay gated by principle 11. Treat snapshot-backed actions as "reversible", everything else as "irreversible".
- For work inside a project, a per-task copy-on-write clone (btrfs `cp --reflink` or a git worktree) gives a cheap sandbox and a clean diff to review. [G]

## 8. Layered design suggestion

Assume the model will be fooled sometimes. Each layer must hold alone.

**L0, identity and channels.** Instructions only from authenticated principal channels (PRINCIPLES 10). All external content is wrapped and labeled as data, with provenance kept outside the token stream. This lowers odds; it is not a wall.

**L1, session design (Rule of Two).** Split work into sessions or sub-agents by capability. A "reader" session sees untrusted content and has no write or send tools and no secrets. A "doer" session has tools and secrets but receives only a structured, checked summary from the reader (a typed record, not free text). [G, similar in spirit to the dual-model patterns Willison writes about; I did not fetch a source for that.] Track A/B/C per session; all three at once forces an ask.

**L2, capability gate (exo-gate, extended).** Keep the reversibility and exposure classifier. Add: held capabilities with scope and limits, taint-aware raising of ask levels, per-day spend and action caps, and a hard rule that the model may only tighten. Move the gate out of the agent's process, into the tool broker (L3), so no tool path bypasses it. [G]

**L3, tool broker.** One small process owns all side effects. Skills and MCP servers do not run in the agent's process; they run as children of the broker.

**L4, OS sandbox per tool call.** Default profile: read-only root, a private tmp, write only to the task directory, no network, no access to `~/.exo/keys`, `~/.ssh`, `~/.config` secrets, browser profiles, the log directory. Build it on bubblewrap plus Landlock plus a seccomp filter, as Claude Code and Cursor do (bwrap or Landlock plus seccomp) [S]. Fail closed if the sandbox cannot start (Claude Code's default is the opposite [S]). Skills and MCP servers from outside get a stricter profile, pinned versions and a hash of their tool descriptions; alert and re-ask on any change (rug-pull defense [S]). Use a microVM only for code from untrusted sources that must run real programs [G].

**L5, secrets.** systemd-creds or Keychain feeding a local broker with credential injection (section 4). Agents and sandboxed children hold placeholders only.

**L6, network.** Default deny. One egress proxy with a per-task domain allowlist and logging. Credential injection happens here.

**L7, cost.** Hard budget per task, per day, per month set by the principal (matches the ceiling in PRINCIPLES resolution 1). Enforced in the broker and, where the provider allows, by a provider-side spend limit [U]. cgroup limits for local CPU and memory. Kill and ask on breach.

**L8, undo.** Snapshot before reversible-with-effort actions; per-task clones for file work. Irreversible classes stay gated.

**L9, log.** Hash-chained, signed by a separate signer, single writer, anchored off-machine on a schedule (section 5). The log daemon is outside every sandbox.

**L10, review.** Weekly digest of asks, denies, near-misses, spend, egress hits and any skill or MCP change. Ask rates feed back into policy (section 3).

### 8.1 Order of work [G]

1. Move secrets out of `keys.env`; add the broker with injection. Biggest risk cut for the least code.
2. Default-deny network for tool children plus the egress proxy.
3. bwrap plus Landlock profile for the shell and file tools.
4. Separate log signer and off-machine anchor.
5. Capability grants and taint tracking in the gate.
6. Reader/doer session split.
7. Skill and MCP pinning and drift alerts.
8. Snapshots in the gate; spend caps.

### 8.2 Open questions

- Which Exo tools are in-process Python today and cannot be moved into a child without work? (Not checked; I only read the gate hook.)
- Does Hermes pass enough context (taint, session) to `pre_tool_call` for L1 and L2? The hook gets `session_id` and `task_id`; taint would need to be added. [G]
- macOS: Seatbelt (`sandbox-exec`) is deprecated but still used by Chrome, Cursor and Claude Code [S via Cursor post]. Is a Linux-only strong profile acceptable at first?
- Per-user cost of a microVM tier on a normal laptop: not measured, no numbers here.

## 9. Risk to control table

| Risk | First-line controls | Backstop |
|---|---|---|
| Prompt injection via web, email, files | L0 labeling; L1 reader/doer split; taint-aware asks | L6 egress deny; L4 sandbox; L9 log for forensics |
| Lethal Trifecta in one session | Rule of Two tracking in gate; split sessions | Ask on any outbound action after untrusted input |
| Confused deputy | Scoped capabilities; recipient and amount shown in the ask | Out-of-band confirm for the worst class |
| Malicious skill or MCP server | L4 strict profile; pin and hash descriptions; run as broker children | Drift alert; remove network from the child |
| Secret exfiltration | L5 injection broker; no secrets in context; creds dir denied to children | L6 allowlist; key-shape scanner; rotate |
| Runaway cost | L7 caps per task and day; provider-side limit | cgroup limits; kill and ask on breach |
| Excessive agency (OWASP LLM06) | Capability grants, least privilege tools | Gate by reversibility; unanswered means no |
| Log tampering | L9 separate signer, single writer, append-only attribute | Off-machine anchor of chain head |
| Self-modification of policy or charter | Gate and policy files read-only to the agent; changes as reviewed diffs | Log entry for any change attempt |
| Approval fatigue | Capabilities instead of clicks; ask budget; small checkable asks | Ask-rate review; canaries (idea only) |
| Local damage from a bad action | L8 snapshots and clones | Backups; irreversible classes stay gated |
| Sandbox fails to start | Fail closed | Alert |

## 10. Limits of this note

- No hands-on tests were run. Nothing here is a measured claim about Exo.
- Codex, gVisor, Firecracker and Firejail points rest on secondary sources, listed above. Check official docs before design decisions.
- Rekor, OpenTimestamps, Flatpak portal, sops and kernel keyring details are from memory and marked [U].
- Willison's primary post, Meta's primary post, OWASP's own site and Anthropic's engineering post were reached through search summaries, not read in full here.
