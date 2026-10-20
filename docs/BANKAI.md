# Bankai: Exo OS as the full release of Exo

Status: guiding document, 2026-10-13. Written by saga-divan from A's direction, for everyone who builds Exo OS
(first gpt-forge). It is not a spec and it decides nothing on its own. Where it suggests a change it says
**Proposal**; a proposal becomes real only as an `OS-NN` entry in `DECISIONS.md`, or as A's ruling where it says
**needs A**. Rulings are A's. Technical judgement belongs to the builders.

How to read it. Section 1 says what Exo OS is for. Section 2 lists the rules it may never break. Section 3 maps each
system from the harness to the OS. Section 4 lists the holes only the OS can close. Sections 5 and 6 are the spirit
and the wider role. Section 7 is the road. Section 8 is how we work. Every claim about code names its source: an Exo
decision (`D-0NN`, in `aska-digital/exo` `DECISIONS.md`), an Exo OS decision (`OS-N`, in this repo), an issue or a
pull request (`exo #N` or `exo-os #N`).

---

## 0. The idea in one paragraph

A's words: "is it intended to be the extreme and total manifestation of the goals, principles, systems, vision,
intention, mechanics, and ethos of project exo. if exo harness is shikai (from bleach my favourite manga) then exo os
is bankai". Yes. The Exo harness is one program that keeps promises about a person's work: it keeps a signed record,
asks before anything that cannot be undone, knows who is speaking, and learns only from the person's own words. It
keeps those promises inside its own process, and every one of its documents ends with the same honest line: a program
running as the same user can still do anything the user can. Exo OS is the same promises, kept by the whole machine.
The gate becomes a wall the kernel enforces. The record gets a keeper the agent cannot overrule and a witness on
another machine. "Who is speaking" becomes an operating-system identity. Same spirit, full power.

---

## 1. Shikai and bankai

### 1.1 The reference

In Bleach, A's favourite manga (by Tite Kubo), a fighter's sword has a spirit inside it. The first release of that
spirit is called *shikai*: the sword takes a new form and gains a power. The full release is called *bankai*: the same
spirit, vastly stronger, and it changes the field around the fighter, not only the blade. Bankai is not a different
sword. It is the same soul with nothing held back.

We use the two words as a frame for this one document. No component, command, package or brand of Exo is named after
anything in Bleach.

- **Shikai is the Exo harness**: one core process, `exod`, with its clients, plugins and store
  (`aska-digital/exo`).
- **Bankai is Exo OS**: the same core, the same protocol, the same store format, installed as the heart of a whole
  machine, with the operating system enforcing what the harness can only check (`aska-digital/exo-os`).

### 1.2 What Exo is

Exo is one system that gives a person what they need to work well with models. It rests on a handful of ideas, each
built and tested in the harness:

- **A record, not a memory.** Every meaningful event goes into an append-only ledger. Each entry is *signed* (a key
  proves who wrote it) and *hash-chained* (each entry holds a fingerprint of the one before, so a removed or changed
  entry breaks the chain). Everything else is a view rebuilt from the record (whitepaper §3).
- **A store that knows you.** One folder per person, the *principal store*: a one-page pack, beliefs with evidence,
  policies, items, the ledger (D-011). The *principal* is the person Exo works for.
- **One continuous memory.** A seat is one agent with one log and one summary tree; windows are threads of one mind
  (D-028).
- **Learning that has to prove itself.** Corrections become typed proposals; selected changes must beat the current
  setup on a held-out exam (D-012, D-022). A new user starts from a house taste and Exo moves toward them slowly
  (D-044).
- **Gates by consequence.** Reversible, private acts run and are logged. Outward, irreversible, costly or third-party
  acts ask, and the answer covers the exact action (D-033, D-038).
- **One core, many screens.** `exod` speaks one numbered, replayable event protocol; the terminal, web page, desktop,
  voice and phone are all clients (D-010, D-014).
- **A harness that watches itself.** Every model call, error and slow turn leaves a trace; findings reopen when a
  "fixed" problem returns (D-025).
- **Fit one person, work for anyone.** The mechanism is generic; everything about a person lives only in their store
  (D-004).

### 1.3 Why the OS is the total manifestation

The harness is one process among many on a computer it does not own. Its checks are application checks: it decides who
is calling by a credential file and a process tree (D-039, D-060), it protects the store by asking before writes it can
see (D-038), it anchors the ledger head in files the same user can rewrite (D-040). Each of these decisions ends with a
"Limits" line that says the same thing: same-user code can go around it, and "the real fix is an OS sandbox" (D-038,
D-039, D-040, D-052, D-060, exo #31, whitepaper §26).

An operating system owns the things the harness can only ask about: which user a process runs as, which files it can
see, whether it can open a network socket, which kernel booted, what happens at login. So the OS can turn each promise
into a property of the machine. That is the line this document draws everywhere:

> **The harness can promise. The OS can enforce.** The OS may make anything the harness allows stricter. It never
> makes anything the harness forbids looser.

| Exo idea | Shikai: what the harness promises | Bankai: what the OS enforces | Where Exo OS is today |
|---|---|---|---|
| The gate | Classifies each call and asks before outward or opaque acts (D-033, D-038) | Every tool call runs in a kernel-built box with only the workspace, no network, no secrets, no control sockets; a call the gate did not pass cannot happen at all | Required executor seam merged in Exo (exo #70); Linux box built in draft exo-os #27 (OS-15) |
| The signed ledger | Signed, chained, pinned keys, head anchor (D-040) | A separate OS account holds the keys and the true copy; root actions carry receipts signed by the process that did them; a witness on another machine keeps checkpoints | Authority contract merged (exo #80); `exo-ledger` account in draft #31 (OS-17); receipts on main (OS-4, OS-11 in #26); witness opt-in in draft #43 (OS-18) |
| Principal authority | The principal comes from the connection, not the message (D-039, D-052) | The principal is an OS identity: a login, a kernel-checked peer, seats that run as separate users | exod as the person's user service on main; peer auth harness half in exo #109 (D-060); seat isolation is a proposal (section 4) |
| Local-first | Store and keys stay on the machine; keys never cross the protocol (D-034, D-050) | Data physically cannot leave without passing the gate: tools have no network, credentials are injected by a separate service after exact approval | Tool network denial in #27; credential egress in draft #28 (OS-16) |
| Taste | Learned and house taste shape how the agent works (D-044) | Taste shapes the whole machine: the surface, wording, interruptions, layout, language | Machine profile changes are signed and undoable (OS-5, OS-6); system-wide taste not started |
| Event activation | Triggers start runs from files, mail, webhooks, git (D-057, proposed) | The OS's own events (wake, login, network, power, a failed service) are a trusted trigger source | Not started; D-057 names the `system` source as needing Exo OS |
| The exosphere | A living figure in the terminal and web clients (D-062, D-063) | A living presence on the desktop that shows what Exo is doing, never nags | Not started |
| Reversibility | The gate grades by undo cost (P11) | Snapshots before system changes, git undo for the profile, a boot entry for an older system | Change engine on main (OS-5, OS-6); admin boot recovery in draft #29 (OS-14) |
| Spend | Cost in the trace; unknown is never zero (D-032) | A protected spend journal starts at zero and stops paid calls at the cap | Draft #34 |
| The store as home | The store is one portable folder (D-011) | The machine is a view of the store: move the store and the machine becomes yours | Store on its own btrfs subvolume (OS-1); machine repo in the store (OS-6) |

### 1.4 What does not change

Bankai is the same soul. These hold in the OS exactly as in the harness:

- **No fork.** Exo OS installs the core at a pinned commit (`exo.pin`, OS-10). Anything the OS needs from the core goes
  to Exo first, as a seam any install can use (D-020, DESIGN.md §10). Ideas that make any Exo safer (the broker, the
  authority, capability grants) live in Exo; Linux wiring lives here.
- **Same protocol, same store.** A store made on Exo OS opens in plain Exo, and the other way round.
- **Same principles.** The OS adds enforcement, not new values. If a design for the OS needs a principle the harness
  does not have, that is a change to Exo's principles, made there.

---

## 2. Principles carried over, as invariants

An *invariant* is a rule that must hold in every state of the system, after every change. Each one below names its
source, says what it means on the OS, and says how to check it. An image that breaks one is not Exo OS, however good it
looks. Tests that prove these belong in the acceptance scripts.

**I-1. The principal comes from the connection, never from the message.**
Source: D-039, D-052, D-060 (exo #109). A caller is the principal only by how it connected: in process, or over the
Unix socket with the per-launch launch record, from a process the kernel says is this user and is not in the core's
tool tree. What a request body says about its sender decides nothing. What the code enforces at the pin (exo
`8600bce`, which includes D-060):
- *Core side, surface path.* The surface joins as `Client(principal="surface")` (`surface/conn.py`,
  `_default_client`). That client, in the pinned core (`exo/principal.py`, `verify_daemon`, `stamp`; `exo/peerauth.py`),
  checks the kernel peer of the connection against the launch record before the credential goes on it, and the core
  stamps the principal only for the same user and a process that is not a descendant of the core. This code is Exo's,
  not this repo's; it is the principal client path, not a property of the OS image.
- *Root helper.* exo-sysd checks `SO_PEERCRED` on every connection (`sysd/protocol.py`, `peer_credentials`;
  `sysd/server.py`, `Server._client` and `_allowed`) and refuses a peer outside its group. No unit test covers `_allowed` (it can return true for every peer with
  `tests/test_sysd.py` still green); the only check is the VM root-door test in `tools/accept-phase2.sh`, which
  exercises the socket mode, not `_allowed` itself. The egress and ledger
  services check the kernel peer too: egress admits only the fixed `exo` desktop UID (`egress/server.py`, `Server`,
  `main`), the ledger service checks the peer UID (`ledgerd/server.py`), and their clients verify the service
  (`egress/client.py`, `verify_socket`, `verify_greeting`; `ledgerd/client.py`). For an append as the `principal`
  writer the ledger service also requires the very process the core's launch record names: the caller sends the
  record as an open file, and the kernel peer's pid and process start identity must match it (OS-21;
  `ledgerd/server.py`, `core_record`, `Server.bind_core`). Every other writer still admits any process of the
  desktop UID.
- *Tools.* The tool namespace has no store, home, compositor, bus or root-helper socket, and denies socket creation
  and connection (`sandbox/runner.py`, `sandbox/restrict.py`; `docs/TOOL-ISOLATION.md`).

Not covered. The principal's other same-user desktop applications are trusted: nothing stops one from connecting to
exod, the root helper or the egress socket as the desktop user, or from reading the owner-only launch record and being
stamped `principal`. The pinned core's D-060 Limits say so: same-user code outside the core's tree remains trusted and
can read the record (`exo/principal.py`, `stamp`, checks the token, the uid, a live and unreused pid, and that the
caller is not a descendant of the core). Nothing in this repo closes that gap; the proposed fix is P-5 (desktop apps in
their own boxes, section 4; see also `docs/TOOL-ISOLATION.md`). Issue #16 (a replaced socket) is closed and is covered by
D-060 `verify_daemon`. OS-21 (issue #106) binds only the ledger's `principal` writer to the core process, and the
same desktop code can rewrite the record it relies on, or shadow it in a private mount namespace, so it does not
close this gap either; the writers that may still attribute events to the principal from any `exo` process are
proposed OS-22. The authority of the principal (approvals, learning signals, settings, model choice, policy
acceptance) rests on the core's stamp, so on the OS the invariant holds for the surface and the root helper, not yet
against a hostile same-user application. Check: a tool child, a detached grandchild and a replaced socket each try to
act as the principal through the surface path, and each fails; another desktop app trying the same is a known gap
until P-5 is built. Any process but the one the record names that asks the ledger to sign as `principal` is refused,
also with the real launch record in hand (`tests/test_ledger_principal_core.py`; on a booted image, pending); a
desktop app that makes a record name itself is the same known gap.

**I-2. Provenance: every message and every record says who wrote it, stamped by the way it came in.**
Source: D-040 (authors), D-010 (`PROVENANCE.toml` for lifted code), exo-os #88 and #90 (build inputs). Authors are
`principal`, `agent`, `tool`, `peer:<name>`, `bus:<seat>`, `job`, `delegate`, `schedule`, `system`, and in the
event-activation design `trigger:<id>` (D-057). Never from the text. On the OS this extends down the stack: a root
action carries a receipt signed by exo-sysd (OS-4, OS-11); an image carries the exact Exo OS and Exo commits it was
built from (OS-13, #30); a ledger row is signed by a writer key that the authority's manifest allows for that author
(OS-17), and the `principal` writer's key signs only for the core process (OS-21). Check: `exo log verify` names every writer; an image's receipt matches its source.

**I-3. An approval is bound to the exact action.**
Source: D-033, D-020 (required approval tokens), OS-12, OS-16. For an outward act the gate hashes the tool, target,
text and all arguments; a "yes" covers that hash only, and an edit is a new ask. Under the required executor the broker
gets a frozen copy of the arguments, and each pending approval has its own token, so two identical calls get two
answers. Standing grants are scoped, expire, are written to the ledger, and are the principal's alone; credentials
never get one. On the OS the card shows the exact request in full and answers only that preview (exo-os #51,
`docs/SURFACE-RECOVERY.md`). Check: change one byte after approval and the action is refused.

**I-4. Esc never stops work, and dismissing is not deciding.**
Source: D-043 (A's ruling), OS-12, D-045. Esc closes menus and leaves panels. It never stops a turn, never answers a
card, never denies. Only an explicit Yes or No sends a decision; an uncertain answer is reconciled against the pending
list and never resent automatically. Stopping is deliberate (ctrl+c, `/stop`, a Stop button). On the OS this holds for
every client the machine ships, including voice ("stop" is a command, not a reflex) and the phone. Check: ordinary
typing, a stray Esc and a reconnect each leave the approval pending.

**I-5. Local-first, and keys never cross the protocol.**
Source: D-050 (consumer path), D-034 (keys are write-only, written by the client, never in a protocol message),
D-011 (embeddings run locally), D-051 (vendor CLIs own their tokens). The store, ledger, memory and keys live on the
person's machine. Nothing is pushed anywhere without the gate. No hosted service of ours holds a user's keys or memory.
What the code enforces at this repo's main:
- *Tool processes hold no key and have no network.* They run in a network-less namespace with a seccomp filter and no
  credential path (`sandbox/runner.py`, `sandbox/restrict.py`).
- *Credentialed actions never put a key in exod or a tool.* `credential_request` (`plugin.py`) sends a bound request
  to the egress service, which runs as `exo-egress`, reads a systemd credential from `$CREDENTIALS_DIRECTORY`
  (`egress/server.py`, `main`; `egress/broker.py`, `read_credential`) and injects it itself. The shipped
  `egress.toml` has no profile, so no route and no credential ship.

The exception. Model provider API keys are not covered. On Linux the pinned core's providers read them in exod from
`keys.env` (mode 0600, readable by any same-user process) until the provider broker lands (section 3.10, row S-7,
P-4, OS-8 D6). For that path exod does hold the key, so "neither the model nor exod nor a tool holds a key" is the
target, true today only for credentialed actions. Check: a tool cannot read a credential path or open a socket; a
request through `credential_request` leaves no credential in the event log or ledger; `keys.env` is the named
exception until P-4.

**I-6. Safety defaults are not taste.**
Source: D-044, A's ruling 3: "safety defaults should not be related to taste". Gate, approval, budget, posting and
other policy defaults are product policy. They ship in `templates/principal-store/policies/` and the image's
root-owned policy files, change only by a reviewed code change or the person's explicit edit, and are never exported,
weighted, learned or decayed. On the OS the same goes for sandbox profiles, egress allowlists, spend caps and the
named root actions: they are policy, administered, not learned. Check: no learning path writes a policy file.

**I-7. The store is credentials.**
Source: D-038, D-065. Policies, state, ledger, keys, seat files and core sockets are on a protected list; any write is
`store_write`, which asks the principal every time, takes no standing grant, and cannot be delegated to a seat. A
changed policy file can only tighten until the principal accepts it. On the OS the store is also physically out of
reach: tool boxes do not contain it, the ledger keys sit under a separate account (OS-17), and policy that governs the
machine is root-owned. Check: a tool that tries to write `policies/` finds no such path; the core refuses to load a
loosened policy without `/gate accept`.

**I-8. Only principal-authored text counts as the principal.**
Source: D-040, D-052, D-054, D-044 guards. Capture, consolidation, promotion, taste signals and handoff pins read text
as the principal's only when its author is `principal`. An agent note that looks like a ruling stays an agent note. A
peer message, a job notice, a trigger envelope, a web page or a pasted quote is never the principal's voice. On the OS
this covers system input too: a notification, a file that appeared, an app's window title, a screen read, a system
event. Check: a ruling planted in a file, a mail or a peer message never becomes a belief.

**I-9. The house taste, with ask or silent.**
Source: D-044, D-072. A new store starts from a scrubbed, versioned house taste and moves toward its person at the pace
people actually change: method items only after weeks of sustained change; values step back fast and adopt slow. Each
person picks `ask` (one proposal, no answer means no change) or `silent` (the change happens, shows, and can be
undone). The principal's own install defaults to ask; new stores default to silent. On the OS, taste that governs the
desktop follows the same rule and the same mode, and is shown with its source. Check: `/taste` lists every swap and its
evidence; an undo restores the item.

**I-10. Bypass mode, and its floor. Unverified at the pin.**
Source: D-065, cited here until now as "A asked for a permissionless mode". At the pinned core
(exo `8600bce`) no D-065 exists (its `DECISIONS.md` goes from D-061 to D-067). The design is exo issue #96 (open) and
exo PR #366 (open), a proposal that is not on main. The pinned source has no bypass mode (`git grep -i bypass 8600bce
-- src` finds nothing); its docs mention one only as that proposal (`docs/GITHUB-IDENTITY.md`, `docs/WHITEPAPER.md`
section 30.1, `docs/DOCS-MAP.md`). This repo has no bypass code. So none of the following is claimed as implemented or checked: that bypass
turns `ask` into `allow` with a banner and a ledger row per call, that a floor still asks for `store_write` and
`credentials`, that `deny` rules still block, that only a live principal connection can turn it on, or that Exo OS
refuses bypass while a required executor is configured. These are the design intent. Until the proposal lands in the core as a decision
or is restated here as an Exo OS decision, treat them as a requirement on any future bypass, not as a
property of the system. Until then the safe reading is that no bypass mode exists. Section 4 carries a proposal (P-6)
for a narrower, contained form that the OS makes safe; it needs A.

Ten more that the OS must also hold, because the harness already does:

- **I-11. Mechanism apart from person** (D-004). The code and the image name no person, and a fresh
  image with an empty store should run end to end. They do name one role account, `exo`, the image's single desktop
  user (`image/mkosi.postinst.chroot`, `useradd ... exo`; `image/mkosi.extra/usr/lib/environment.d/50-exo-os.conf`,
  `EXO_PRINCIPAL=exo`, `EXO_HOME=/var/lib/exo/exo`; `egress/server.py`, `main`, `account_uid("exo")`). That account is
  mechanism, not a person: the desktop user is a fixed role the units, paths and peer checks refer to. The image's
  login shell instead derives the principal from `$USER` (`etc/profile.d/exo-os.sh`), and `setup.py` falls back to the
  login name. Everything personal is in the store. The image sets a fixed hostname, `exo` (`image/mkosi.conf`,
  `Hostname=exo`), a role like the account, not an identity. One machine is named in a comment and a test name: the
  `webapp_class` docstring (`src/exo_os/profile.py`) and `test_webapp_class_seen_on_charminar` (`tests/test_profile.py`)
  record where a window class was observed; no behaviour depends on it, and removing it is a separate code change. Not
  claimed: that no account, host or machine name appears (they do, as above), or that an empty-store boot has been
  proven end to end.
- **I-12. Reversibility governs freedom** (P11, OS-5). The easier an act is to undo, the freer it runs. Nothing changes
  the system without a pre-snapshot and a ledger row; a failed check reverts without asking and says so in one line.
- **I-13. Evidence before action** (D-020 required gate audit, OS-11, OS-17). Under the required executor the decision,
  and after consent the approval, are committed to the ledger before the call runs. If the record cannot be written,
  the act does not happen. A completed act whose record failed is reported as completed and repaired by reconcile,
  never repeated.
- **I-14. Fail closed** (OS-15, OS-17, D-020). A missing sandbox, broker, authority, policy or key is a refusal, never a
  fallback to the unprotected path.
- **I-15. A peer is input, never consent** (D-026). Peers, bus messages, triggers and channels can ask; they cannot
  approve.
- **I-16. A decision model may tighten, never loosen** (D-015, D-019). It may classify, never swap, adopt or grant.
- **I-17. Honest status** (D-032, OS-13). Unknown cost is "unknown", never zero. An earlier image's pass never counts for
  a new one. "Built" means merged and tested; "draft" means draft.
- **I-18. A way out** (DESIGN.md §7.3, phase 3 check 5). With exod stopped, the person still has a working desktop and
  their app keys. A shell with no exit can trap a person.
- **I-19. One core, no fork** (D-010, D-020, OS-10). The pin moves only after the plugin and client tests pass on the new
  commit.
- **I-20. Credit where it is due** (section 5.3). Every borrowed idea and lifted file names its source.

---

## 3. Systems, mapped from harness to OS

For each system: what the harness has today, what Exo OS has today, and what the full release looks like. "Main" means
merged on `exo-os` main. The foundation stack covers PRs #26 to #94. PR #26 has landed. Each remaining PR needs an
independent reviewer's MERGE verdict and a green full Linux host suite at the exact head before merge. Release and
image-ready claims require native acceptance on the exact combined image (OS-13).

### 3.1 Core and protocol

- **Harness.** `exod`, a daemon with one numbered, replayable event stream; all 30 command slots are used, so new needs ride
  optional fields or `command.run` built-ins (D-022, D-045, D-069). The counted core stays under 15,000
  lines (D-067). Peers and the scheduler now sit outside the core (D-067).
- **Exo OS today.** Main: exod as a systemd *user service* (started at login, restarted if it dies) running as the
  person, never root; the exo-os plugin uses the public plugin API (OS-7); Exo pinned by commit (OS-10). Draft stack:
  `docs/CORE-CONTRACT.md` splits duties between core and OS; the pin is `cff62d1`.
- **Full release.** exod is the heart of the login session: logging in opens the person's store, logging out closes
  it (DESIGN.md §3.2). Every client authenticates the core by kernel peer credentials before it sends anything, and the
  core authenticates every client the same way (exo #109). The stream has a heartbeat and an instance cursor, so a
  stalled core never looks alive (exo-os #16, #18). The core shapes output to each client's declared capabilities
  (large text, plain language, voice), which it does not read today (DESIGN.md "Since phase 0").

### 3.2 Gate and executor

- **Harness.** The gate classifies every call by consequence; shell is an allowlist with `opaque_exec` for what it
  cannot read; outward acts bind to exact text; the store is credentials (D-033, D-038). The *required executor* seam
  (exo #70, D-020): with `EXO_TOOL_EXECUTOR_REQUIRED=1`, every registry call from turns, subagents and schedules passes
  the gate, schema check and then one installed broker; failures refuse; MCP startup and remote delegation are refused;
  the gate must commit its evidence first.
- **Exo OS today.** Main: tool names chosen so Exo's own gate places them (OS-7); `system_package_install` asks. Draft
  stack: the Linux adapter (#27, OS-15). File, search, patch and foreground shell calls run in a fresh *bubblewrap*
  box (a set of Linux namespaces, so the process sees its own private view of files, processes and network), with
  *Landlock* (a process gives up file access it does not need) and *seccomp* (forbidden system calls are blocked):
  only `/home/exo/Workspace` is writable, no network, no Unix sockets, no store, keys, browser profile, compositor or
  helper sockets, timed-out trees killed. Plugin packaging for required mode (#41).
- **Full release.** Every side effect Exo causes leaves through a door the OS built. The order of doors (DESIGN.md
  §4.1) becomes a set of reviewed adapters: a real API through the egress service, a file through a granted handle,
  an app through a structured window-manager action, the screen only when asked, typing for the person only with
  consent. MCP servers, background jobs and cross-machine delegation run in their own boxes instead of being refused.
  Providers and plugins, which today run as trusted code inside exod, move out of process (P-4). The gate's system
  categories (`sys_profile`, `sys_package`, `sys_service`, `sys_reboot`, `sys_boot`, `sys_update`, DESIGN.md §5.2)
  land in Exo as a policy extension, as OS-7 already says, so any Exo can use them.

### 3.3 Ledger and authority

- **Harness.** Signed, hash-chained, append-only; pinned keys and a head anchor (D-040). The *ledger authority*
  contract (exo #80): a separate service owns the journal and keys, commits each event, checkpoint and operation id
  before it signs, and verification replays its pinned identity read-only. The witness was taken out of the core until
  something deploys it.
- **Exo OS today.** Main: exo-sysd signs a receipt for every root action and the caller writes it to the ledger
  (OS-4). Draft stack: kernel-authenticated helper, nonce-bound receipts, a durable outbox and `exo-os system reconcile`
  (#26, OS-11); the `exo-ledger` account on the persistent `@signer` subvolume, outside system rollback, provisioned
  before first login and refusing on missing or mismatched state (#31, OS-17); an opt-in administrator witness
  publisher over pinned mutual TLS (#43, OS-18).
- **Full release.** One verifiable history of the machine: every model turn, gate decision, approval, profile change,
  root action, credentialed request and spend reservation is a row the agent cannot forge or erase. A *witness* (a
  second party that keeps signed checkpoints, so a rollback of the whole store shows) runs somewhere the machine's own
  root cannot reach: the person's second device, a home box, or a service they choose, never a server of ours that
  sees content (D-050). Anyone with the store and the authority's public identity can verify it on another machine
  with plain Exo.

### 3.4 Memory and taste

- **Harness.** One agent per seat, one log, one summary tree, threads as focus (D-028); principal capture as a
  mechanism (D-032); turns never wait for upkeep (D-068); the learning loop with promotion rules and held-out climbs
  (D-012, D-022, D-054); the house taste with the sustained-change rule and ask or silent (D-044, D-072).
- **Exo OS today.** Main: the machine profile is a git repo in the store; every change is a signed ledger row with
  author and phase (OS-6); `exo-os today` and `timeline_show` read the day back in plain words.
- **Full release.** Taste is system-wide. How the desktop speaks (language, length, 12 or 24 hour time), how often it
  interrupts (an interruption budget, P3), what the prompt bar offers first, where panels open, which apps start: all
  are taste items with sources, learned under I-8 and I-9 and shown in `/taste`. Desktop signals feed the same loop: an
  undo of a profile change is a verdict, a dismissed panel is a `ui.respond`. Only the principal's own acts count; an
  app's behaviour never does. The store is the person's real home: copy it to a new machine and the machine becomes
  theirs (DESIGN.md §3.3).

### 3.5 Seats and peers

- **Harness.** Seats are agents; seats answer approvals only with a signed answer, never `allow_always` (D-033); peers
  enter through one ingress path, held by default from other principals (D-026); a seat's bus key is pinned on first
  sight (D-040).
- **Exo OS today.** One principal per login (DESIGN.md §9.1); the `exo` account; the *helper* (someone who looks after
  the machine) is an approver for named categories through `approvals.yaml`, never a second principal (OS-8 D7).
- **Full release.** Seat isolation is an OS fact (P-1): each seat runs as its own system user with its own store
  directory and its own key, generated on the machine it runs on, so one local agent cannot read another's memory or
  sign as it. This also closes a class of gap where seat keys minted on one machine let any local agent there sign as
  those seats. The principal is the Linux login; a helper's changes show to the principal in plain words
  ("Your helper made the text larger. Undo?", DESIGN.md §9.2). What a helper may read is A's call (OS-8).

### 3.6 The bus

- **Harness.** `exo.bus`: the Divan Bus signed relay; envelopes checked against pinned seat keys; bus messages framed
  with author `bus:<seat>` and held by default (D-016, D-040, D-041).
- **Exo OS today.** None on the image.
- **Full release.** The bus endpoint is a small system service under its own account holding the machine's seat keys,
  so a desktop app cannot sign as a seat. "Ask my helper" is one button that sends the helper what happened and the relevant
  log lines (DESIGN.md §9.3). The bus is also a natural carrier for witness checkpoints between a person's own machines
  (P-7), with D-040's caution kept: not every home has a bus, so it is one option, never the only one.

### 3.7 Scheduler and event activation

- **Harness.** Scheduled jobs as ordinary turns, outside the core since D-067. Event activation is designed and not
  built (D-057): a *trigger* is a job with `on` instead of `when`; connectors pass an allowlist of fields and mask
  sensitive values into placeholders that resolve only at the gate; trigger turns have author `trigger:<id>`, run under
  the gate with rate and cost limits, and a failed run gets a root-cause finding. D-057 names a `system` source (wake,
  login, network change, power, a device plugged in, a service failing) that "needs Exo OS".
- **Exo OS today.** None.
- **Full release.** The OS supplies that `system` source from systemd, logind, udev and NetworkManager, as a trusted
  local feed. The machine heals itself through it: a failed service or a broken update raises a trigger, the run
  proposes the undo, and the person sees one line ("WhatsApp stopped working after an update, so I put things back",
  DESIGN.md §9.3). System events are data, never the principal (I-8), and the agent may propose a trigger but never add
  one (D-057 rejects that).

### 3.8 Clients: terminal, web, desktop

- **Harness.** The Textual terminal client and the web client, both with the site's look (D-031, D-061), the composer
  and completion (D-037, D-046), the choice card (D-066). A desktop app is chosen but comes after the core and browser
  settle (D-055).
- **Exo OS today.** Main: a GTK4 layer-shell prompt bar, panel drawer and approval card, an Exo protocol client that
  joins as `principal:surface`. Draft stack: pending requests keep typing; F6 enters review; Esc defers; keyboard
  accessible panels (#35, #51, #94).
- **Full release.** The desktop is the surface: one key drops the prompt bar, which is both launcher and question line
  (DESIGN.md §7.4). Panels open only when they help, each with a text form for screen readers and voice. The terminal
  and web clients keep working on the same core. Voice and phone are clients of the same protocol (phase 5, exo-os #9):
  the phone is the second channel for the worst class of ask (money, deletes, publishing), a channel injected text
  cannot also answer. Orca reads every card (#7).

### 3.9 Exosphere and x0

- **Harness.** The *exosphere* is Exo's evolving 3D figure, drawn from the incentre lattice (D-049); its forms are x0
  (the egg), then x, then 3xo, with more to be named (D-063). It sits in the terminal's corner and on the web chat page
  and moves with the agent: idle, thinking, tool running, waiting on approval, error, done (D-062). x0 on the site as a
  live community egg and personal growth from the verified ledger are proposed (D-048, D-058, D-059).
- **Exo OS today.** None.
- **Full release.** The exosphere is the living desktop presence: a small layer-shell figure that breathes when Exo is
  idle, pulses when it thinks, carries packets while a tool runs, glows when it waits on you, and blooms when work is
  done. It is the visible sign that the machine is a companion, not a pile of programs. It obeys the brand rule that
  motion only reports state, and the exopet rule of no care stats, needs, decay or nags (D-048). It never takes focus,
  honours reduced motion, and fits a CPU budget (D-062 measured +0.25% of a core idle in the terminal). Its personal
  growth, once D-058 is accepted, is a pure function of the verified ledger, so it cannot be faked by editing a file.

### 3.10 Providers and inference

- **Harness.** Provider and model agnostic; every role is user config (D-018); tiered subagents (D-017); decision
  models, Jev first and Laya local (D-019); keys written by clients only (D-034); keyless CLI providers (D-051).
- **Exo OS today.** Draft stack: a protected spend journal that starts at zero paid spend, root-owned policy, paid
  routes refused until an administrator prices them (#34). Main: Exo's Linux key path, `keys.env` 0600, until the
  secrets layer lands (OS-8 D6).
- **Full release.** Keys live in `systemd-creds` (TPM-sealed where a TPM exists) and are injected into provider calls
  by a broker, so exod never holds them. Local inference fits the machine's tier (DESIGN.md §11): speech, voices, a
  small embedder and rules always local; reasoning remote on small machines; larger local models on a home box. Local
  models run as separate, capped processes started on demand, never inside exod. Own inference, a keyless path, comes
  later and only as D-050 allows.

### 3.11 Updates and versioning

- **Harness.** SemVer with a codename per major from the exopet line (D-056); `exo update` with rollback for detached
  checkouts (D-064); opt-in signed updates against a locally pinned trust list (D-073).
- **Exo OS today.** Main: images built from text config with mkosi, unprivileged; Exo pinned by commit. Draft stack:
  source and pin provenance in every build, acceptance bound to that exact image (#30, #90, OS-13).
- **Full release.** An update is a new signed image or a signed package set, checked against a trust list the person
  pinned (D-073's rule carried to the whole system), applied with a snapshot before, and undoable from the boot menu.
  The kernel image is signed for Secure Boot (#22). The pin moves only after contract tests (OS-10). The base may later
  move to NixOS or bootc images for shipping to others (OS-1, phase 8); the gate, ledger and store do not care which.

### 3.12 The sandbox (exo #31)

- **Harness.** None. Exo #31 lists the concrete same-user routes and asks for tool subprocesses to run "in a namespace
  or container ... with no access to `$EXO_HOME` or the core socket".
- **Exo OS today.** The draft stack's tool box (#27) is that sandbox for workspace tools on Linux.
- **Full release.** Section 4 lists each route and how the OS closes it. The test list in exo #31 becomes an OS
  acceptance script that runs in the VM on every image.

---

## 4. The open seams, and how the OS closes them

A *seam* is a place where the harness's promise depends on something it cannot control. A *same-user escape* is a
route by which code running as the person's own user gets around an application check. These are the reason Exo OS
exists. For each: where it is named, what the harness does, how the OS closes it, and where that work stands.

| # | Seam or escape | Named in | Harness half | How the OS closes it | Status |
|---|---|---|---|---|---|
| S-1 | **Write, then run through an allowed program.** `write_file conftest.py` then `pytest`; a git hook then `git commit`; `pip install -e .`, `cargo test`, `go test` | exo #31, D-038 "Not done" | The gate cannot see code a trusted program will run | Every such program runs inside the per-call box: it sees only the workspace, has no network, no store, no keys, no sockets. Writes to `~/.bashrc` or `~/.config/systemd/user/` are impossible because home is not in the box | Built in draft #27; VM proof pending |
| S-2 | **A detached child gets principal identity.** A tool child reads the credential, double-forks out of the core's tree, and is stamped `principal` | exo #31, D-039 limits, D-060 limits | exo #109 (D-060): kernel peer checks both ways, uid check, process start identity, unreadable chain counts as the tool tree; still trusts same-user code outside the tree | The tool box has its own process namespace and no access to the socket or the launch record, so there is nothing to steal and nowhere to connect | Box built in #27; #109 open; surface side is exo-os #16 |
| S-3 | **A same-user process rewrites the ledger and both anchors** | D-040 "Not covered", whitepaper §20.2 | Head anchors, pinned keys; authority contract (exo #80) | Keys and the true journal belong to the `exo-ledger` account on `@signer`; the desktop holds only a projection; a witness on another machine keeps checkpoints, so even a coherent rollback of the store shows | #31 (OS-17) and #43 (OS-18) draft; independent witness not deployed |
| S-4 | **A same-user process edits policies, grants or the key registry** | D-038, D-040 | Writes ask (`store_write`); a changed policy only tightens until accepted | Tools cannot see the store. Policy that governs the machine is root-owned. P-2: grants and the key registry move behind the authority or a store service, so another desktop app cannot edit them either | Tools side built in #27; desktop-app side open |
| S-5 | **A same-user process drives the core** (`turn.send`, settings) or reads the launch record | D-039, D-052 limits, exo-os #16 | Non-principal callers are stamped `local`; their words are theirs | Tools cannot reach the socket. P-5: ordinary desktop apps (the browser above all, which reads the open web) run in their own sandboxes with portals, so they cannot read the launch record or the store | Open |
| S-6 | **A same-user process calls exo-sysd's named actions outside the gate** | exo-os #26 body, `docs/CORE-CONTRACT.md` | Root actions are few, typed and receipted (OS-4, OS-11) | Tool boxes cannot reach the helper socket. P-3: mutating root actions require an approval the authority has committed, so membership of `exo-sys` alone is not enough | Tools side built; P-3 open |
| S-7 | **Keys readable by any same-user process** (`keys.env`) | D-034, OS-8 D6, DESIGN.md §6 L5 | Keys never enter the protocol, events or logs | `systemd-creds`; a separate unprivileged service injects credentials into approved HTTPS requests; tools have no network; replies carry status, never upstream bodies | #28 (OS-16) draft, no credential shipped; providers still read keys in exod (P-4) |
| S-8 | **Installed plugins and providers run as trusted code in exod** | D-020, exo-os #27, #34 limits | Required mode loads plugins only from executor-chosen folders | P-4: providers run out of process with network only to their own endpoint; plugins run behind the broker like tools | Open |
| S-9 | **Spend bypass by same-uid code**, and a coherent rollback of store plus journal | exo-os #34 limits | Cost in the trace; unknown is never zero | The spend journal and its policy sit outside the person's uid, beside the authority; the witness catches rollback | #34 draft; journal still exo-owned |
| S-10 | **Anyone with the disk can swap the kernel** (unsigned UKI); **`exo` is in `wheel`**; **the screen lock has no credential** | exo-os #22, #71 (both `needs decision`) | n/a | Sign the UKI and boot with Secure Boot; ship an explicit sudoers policy or no sudo; set a credential at first boot through PAM, which the agent never sees (gpt-forge's recommendation on #71) | Needs A |
| S-11 | **Data leaves the machine without the gate** | D-050, DESIGN.md §12 lesson 3 | Exo's own sends are gated; posts are scrubbed (D-033) | Tools have no network; Exo's outward acts go through the egress service or a provider broker; the surface shows when data leaves (Jolla's idea). Apps the person uses directly stay the person's own business | Tools built in #27; egress #28; the "data left" view is open |

What remains after all of this, stated plainly. Programs the person runs themselves (a terminal, a browser, an app)
act with the person's authority, and should. Code that Exo wrote into the workspace can later be run by the person
outside the box; the OS can label such files, not forbid running them. An authorized but compromised core can still
submit invented events or leave actions out (whitepaper §20.2). The OS makes these limits small and visible; it does
not pretend they are gone.

### The proposals in this section

- **P-1. Seats as OS users.** Each seat runs as its own system user, with its own store directory and its own key made
  on its own machine. Changes nothing decided; extends OS-8.
- **P-2. Grants and the key registry behind the authority.** Writes to grants and the registry go through the ledger
  authority (or a small store service under its own account), so a same-user app cannot add a grant or pin a key.
  Needs a seam in Exo first.
- **P-3. Root actions need a committed approval.** exo-sysd accepts a mutating action only with an approval the
  authority has committed for that exact request. Extends OS-4 and OS-11.
- **P-4. Providers and plugins out of process.** Providers call out through a broker with network only to their
  endpoint; plugins run behind the executor. Needs a seam in Exo first.
- **P-5. Desktop apps in their own boxes.** Chromium and other apps run sandboxed with portals, so they cannot read the
  store, the launch record or each other's data. Keeps the memory budget in mind (DESIGN.md §11).
- **P-6. Contained bypass (needs A).** the design intent (unverified, I-10; the proposal is exo #96, not on main) is that bypass is refused under the required executor, because that installation
  exists to decide and record every call. The OS changes the trade: a call inside the box can only touch the
  workspace, and the workspace is snapshotted. So the OS could auto-allow *contained* calls (reversible, no network,
  workspace only) without asking, while every outward act, `store_write`, `credentials` and every root action still
  ask, and every auto-allowed call is still committed to the ledger first. This would change an Exo decision, so it is
  A's call, filed with the `needs decision` label before any code.
- **P-7. The witness on the person's own devices.** The first witness is another machine the person owns, reached over
  the bus or a direct pinned link; no hosted service of ours sees checkpoints.

---

## 5. Ethos and experience

### 5.1 How it should feel

Alive, sleek, futuristic and calm. Concretely:

- **Alive.** The exosphere breathes; the machine notices, fixes and says so in one line. A's image for the brand is
  "an alive evolving pulsing matrix of information" (D-049).
- **Sleek.** Instruments, not chrome; hairlines, not boxes; orange is the signal and steel blue is system knowledge
  (Exo `docs/UI.md`). Nothing technical on screen that the person did not ask for; power is reachable, not visible.
- **Futuristic.** One key to ask or launch. The machine is a view of your store. Your history is signed.
- **Calm.** Motion only reports state. Asks are rare, plain, exact and big enough to read. Unanswered means no. No
  care stats, no streaks, no guilt, no notifications that only want attention.

### 5.2 Fit one person deeply, work for anyone

The first real principal on Exo OS is not a developer (DESIGN.md §9.3). If a change makes his day harder, it does not
ship (ROADMAP exit rule). At the same time nothing in the image may assume him, A, or any account (I-11). The test is
both at once: a fresh image with an empty store onboards a stranger, and the principal's own install loses nothing
when the image updates (D-004).

### 5.3 Credit

Exo stands on open-source work and on people who had the idea first. Credit them by name and never wear their brand:
Arch Linux, systemd, mkosi, btrfs and snapper, Hyprland, gtk4-layer-shell, bubblewrap, Landlock and seccomp, Omarchy
(a reference, not a fork, OS-1), NixOS for the build, test, switch loop, Hermes Agent for what Exo lifted (with its MIT
notice), opencode and Command Code for the UI and taste ideas, Railtown AI for event activation (D-057), EvoPet,
TamaHermes and Petdex for the pet (D-048), the incentre lattice project for the geometry (D-049), Simon Willison's
"lethal trifecta" and Meta's "Rule of Two" for the security frame (DESIGN.md §6), gwern's guardian-angel essay for the
principles (D-006). A credit line in the doc and a licence notice in the package, every time.

### 5.4 Who decides what

Rulings are A's: what Exo is for, what it may do with a person's data, spend, model choice, anything outward or
irreversible, and any change to an Exo decision. Technical judgement belongs to the builders: how to build it, which
mechanism, what the tests are. When the two meet, the builder writes the options and the trade plainly and labels the
issue `needs decision`; A rules; the label comes off.

---

## 6. The ecosystem role

Exo OS is the node of a wider loop. Concepts only here; the rest is private.

- **Exo builds Exo.** People run Exo. When their agent hits a problem, it drafts a signed issue or pull request against
  Exo itself, shown to the person as an outward act before it leaves (D-033). This is already happening: the canary
  agent files its own issues and PRs from inside Exo. Exo OS is where this is strongest, because the trace, the
  findings and the machine's own failures are all first-hand, signed records.
- **Credit for accepted work.** A contribution is credited when it is accepted, not when it is made. The first visible
  form is x0: instant XP for repository activity, and most XP granted in "meditation" for validated work, an issue fixed
  by a merged PR, a PR merged, a meaningful review (D-058). Attribution is cheapest and most honest at the source,
  signed by the seat at the moment the work happens.
- **The compute network.** An idle Exo OS machine can do useful, checkable work for the community: re-running a
  contribution's tests and evals on independent machines and comparing results (verification work first), and later
  local inference for non-private work. Private work never leaves the machine (I-5). Opt-in, visible, capped, and
  governed by the same gate as everything else.
- **The forge and the hub.** Agent identity tied to a principal, provenance on every change, machine-readable review,
  cost per change: a forge layer for agents grows from what Exo already records. The hub, where people and agents
  find each other and form projects, starts where Exo already supplies the demand: skills, pets, plugins for Exo.

Exo OS's part is to make every node trustworthy: a machine whose records cannot be forged, whose work can be checked,
and whose owner's data stays home.

---

## 7. Build path

Stages run from where exo-os is today to the full release. They follow the existing phases in `ROADMAP.md` (P4 to P8)
and do not replace them; each stage says which phase it serves. Each stage has exit criteria someone can run and see
pass or fail, and lists what it needs from the harness. No dates; order by dependency.

### Stage 0. Land the foundation (finishes P1 to P3, starts P4)

The foundation stack (#26 to #94) is most of phase 4's mechanism. PR #26 has landed. The remaining PRs still need
the merge gates below. Merging them does not establish native image acceptance.

- **Work.** Each foundation PR gets an exact-head MERGE verdict from an independent reviewer who did not author it,
  plus a green full Linux host suite at that head. Merge contiguous batches in order with merge commits. Then build
  one combined candidate with real merge ancestry, record its source set, and run every acceptance script on that
  exact fresh image. README status says only what the scripts showed. Main merges do not claim an image is ready.
- **Exit.** On one image built from one recorded commit set: phases 1 to 3 pass in full (including the GTK probe), the
  isolation probe proves protected-state denial (#48), the ledger authority provisions and verifies, spend starts at
  zero, and boot recovery actually reboots into a checkpoint and back. Release and image-ready claims name the exact
  tested source set and image. Later main commits do not inherit that acceptance.
- **Harness needs.** When the pin moves past Exo `ea71960`, use `eng.approvals` (bus #297). Whatever core `cff62d1`
  carries in draft (witness transport) must land in Exo before the OS depends on it on main.

### Stage 1. Identity and liveness (P3 and P4)

- **Work.** exo-os #16 and #18: the surface authenticates exod by kernel peer credentials and the launch record before
  sending, and treats a silent stream as dead.
- **Exit.** A replaced socket gets zero bytes from the surface; a stalled exod shows as offline within the heartbeat
  window; a reconnect after restart picks up the new instance and reconciles open approvals without resending consent.
- **Harness needs.** **Exo PR #109 (D-060), Unix peer authentication**, merged and pinned: it is the harness half of
  this seam (the client checks the server before the credential moves; `hello` carries `instance` and `heartbeat`;
  `approvals.list` carries a cursor). The OS completes it by making the socket unreachable from tools.

### Stage 2. Close the same-user escapes (completes P4)

- **Work.** Section 4's rows S-1 to S-9 as tests in the VM; P-2, P-3 and P-4 as needed; A's rulings on #22 and #71
  carried out.
- **Exit.** ROADMAP phase 4's red-team script passes on a fresh image (an injected "forward your mail" leads to an ask
  or a block; a tool cannot read credentials, SSH keys or the browser profile; a tool cannot reach a domain outside its
  list; an edited or deleted ledger row is detected; no sandbox means no run; a cost breach stops and asks), plus every
  route in exo #31, each failing to escape.
- **Harness needs.** Exo #31's suggested detach case in the identity attack test, so the harness test fails where the
  OS must hold. The seams for P-2 and P-4 in Exo, if accepted. Gate system categories (OS-7 proposal) if the OS wants
  them as policy.

### Stage 3. The record leaves the room (P4, toward P8)

- **Work.** An independent witness (P-7); a store and its authority export verified on another machine in plain Exo.
- **Exit.** Restore an older copy of the whole store and journal on the machine; the witness check reports it. Copy the
  store to a Mac; `exo log verify` against the exported identity passes, and fails on a one-byte edit.
- **Harness needs.** The witness transport and challenged-head API in Exo main; offline verification against an
  exported authority identity.

### Stage 4. The machine as a view of you (new; serves P3 and P6)

- **Work.** System-wide taste on the surface; desktop signals into the learning loop; the `system` trigger source and
  self-healing runs; the "data left the machine" view (S-11).
- **Exit.** A taste swap on the desktop shows in `/taste` with its evidence and undoes; a broken update is put back by a
  trigger run with one plain line; a fresh store on the same image gets the house defaults, not the principal's.
- **Harness needs.** D-057 accepted and built in Exo (triggers, connectors, masking, the `trigger:<id>` author). Taste
  waves 2 and 3 (D-044) on Exo main.

### Stage 5. Voice, phone and presence (P5)

- **Work.** Voice and phone as protocol clients; the phone as the second channel; the exosphere on the desktop;
  Orca on every card.
- **Exit.** ROADMAP phase 5 checks; the exosphere's states map one to one to client events, under its CPU budget, with
  a still frame under reduced motion; Orca reads the approval card (#7).
- **Harness needs.** Exo's open item from D-060 on phone factors and D-033's "principal always" (a ruling for A); the
  exosphere engine files from Exo's brand folder; D-058 if personal growth is shown.

### Stage 6. Real hardware (P6, then P7)

- **Work.** Signed UKI and Secure Boot (#22); first-boot credential (#71); USB image (#10); the spare-laptop week; local
  speech and a small local model on tier 1; then the first real principal's machine, only as A decides.
- **Exit.** ROADMAP phases 6 and 7 as written: a week with no lost work, 1 GB free with five web apps, the tester
  answers "what did Exo do yesterday?" from the timeline alone, then two weeks of daily use with a way back at every
  step.
- **Harness needs.** Signed releases of Exo (D-073 moves from opt-in to the image's default once Exo signs).

### Stage 7. Others, and the network (P8)

- **Work.** Images others can install; onboarding builds their store; the base revisited (NixOS or bootc); the
  compute-network role as opt-in verification work.
- **Exit.** A second household installs from an image with no help and `exo init` builds their store; a verification
  job re-runs a contribution's tests in a box and signs the result, with nothing private leaving the machine.
- **Harness needs.** Exo's public release, the house taste pack released (`house-v1`), and the forge-layer receipts.

---

## 8. How to work

- **The bus.** Exo OS work is coordinated on the Divan Bus, thread `exo-os-integration`. Progress, questions and
  verdicts go there, not in GitHub comments. GitHub keeps the lasting record: pull requests with real changes (with
  "Fixes #N"), and issues for real findings.
- **Claims.** Before you start an issue, claim it on the bus (`post_claim`), so two seats never build the same thing.
  Release the claim when you stop.
- **Local-first test flow.** Build on a branch. Run the host suite, then the Linux checks in a disposable container,
  then build a fresh image and run the acceptance scripts on that exact image (OS-13). Several ready changes may be
  combined into one candidate with real merge ancestry for image evidence; each PR keeps its own owner. Never claim a
  pass you did not see on the image you name.
- **Merges.** The maintainer merges the harness (`aska-digital/exo`) pull requests. For exo-os, an independent reviewer
  who did not author the PR reviews each exact head, records real defects as inline comments, and sends MERGE or FIX
  with the comment IDs on the bus. A draft requests that review. The maintainer marks it ready and merges after MERGE
  and a green full Linux host suite at the same head. Use merge commits for contiguous batches, with every blocking
  comment resolved or named. Native VM acceptance is required before release and image-ready claims.
- **Decisions.** Anything that needs A's call gets the `needs decision` label, with the options and the trade written
  plainly in the issue. Remove the label once A rules. A technical choice inside the builders' remit is made by the
  builders and recorded as an `OS-NN` entry in `DECISIONS.md` (next free number across all branches).
- **Docs as you go.** Every change updates its doc in `docs/`, `DECISIONS.md`, and the README's status and "Where
  things are" table in the same pull request. Anything the OS needs from Exo goes to Exo first, with Exo's own docs
  rule (a row in Exo's `docs/DOCS-MAP.md`).
- **Changing this document.** Propose changes on the bus thread. If a stage, invariant or proposal here is wrong, say
  so and say why; this is a guide, and the builders' evidence wins over its guesses.
