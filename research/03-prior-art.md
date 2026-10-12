# 03 Prior art: AI-first OS attempts and agent desktops

Date: 2026-10-12. Status: research note, v1.

Method. Web search plus a few page fetches. Every claim has a link. Where I only
had a secondary source, I say so. "GUESS" marks my own inference. I did not
verify any benchmark beyond what the cited page says. Many sources are news
or blog summaries; check primary pages before quoting outside the team.
Not covered: GNOME/KDE agent features (only their AI contribution policies),
Gemini auto browse, and Siri's later shipping status. I found nothing solid.

Context: Exo principles are in exo-audit/exo/research/PRINCIPLES.md. I refer to
them as P1 to P18.

---

## A. AI gadgets that tried to replace the phone

### Rabbit R1 and the "Large Action Model"
- Tried: a handheld that operates websites and apps for you via a
  "large action model" (LAM).
  [Wikipedia](https://en.wikipedia.org/wiki/Rabbit_r1)
- Outcome: strong criticism at launch; reviewers asked what it did that a phone
  could not. The Verge reported on 30 April 2024 that the software ran on a Pixel 6a.
  On 25 September 2024, The Verge cited the CEO saying about 5,000 of roughly 100,000
  buyers used it on a given day. Same page.
  [Wikipedia](https://en.wikipedia.org/wiki/Rabbit_r1)
- Reported (not proven): the LAM was mostly hardcoded Playwright scripts, and
  they break when sites change. This rests on a video listing and a Substack post.
  I found no primary confirmation and no direct reply from Rabbit.
  [Rosetta listing of Coffeezilla video](https://rosetta.to/person/emily-shepherd),
  [Substack notes](https://xuanvictorluis.substack.com/p/notes-on-rabbit-r1)
- Later: Rabbit shipped a web "LAM Playground" (Oct 2024) and a beta "teach mode"
  (Nov 2024) where you demonstrate a task. Rabbit warned results may be unpredictable
  and CAPTCHA sites cause trouble.
  [Wikipedia](https://en.wikipedia.org/wiki/Rabbit_r1)
- Why: the promise (it acts for you on any site) was bigger than the working
  mechanism. Per-site automation is brittle. A separate device also gave nothing a
  phone lacked.

### Humane AI Pin
- Tried: a screenless wearable with an AI assistant and projector, $699 then $499.
- Outcome: Humane sold most assets to HP for $116M. Reportedly it had sought
  $750M to $1B. The pins lost calling, messaging, AI and cloud access on 28 Feb
  2025. Only buyers within 90 days could get refunds. Humane had raised over $230M.
  [TechCrunch](https://techcrunch.com/2025/02/18/humanes-ai-pin-is-dead-as-hp-buys-startups-assets-for-116m)
- Reasons TechCrunch lists: weak reviews, returns reportedly above sales by summer
  2024, battery fire concerns, a price cut that did not fix demand. Same page.
- Lesson: a cloud-only device is a rental. When the vendor stops, the device dies.
  (Exo link: P1, P18.)

### Open Interpreter and the 01 Light
- Tried: an open source "natural language computer interface" that runs code on your
  machine, plus a pocket voice device (01 Light).
- Outcome (secondary sources only): in September 2024 the team reportedly refunded
  all 01 Light orders, cancelled manufacturing, and moved to a free phone app
  ("It should have been an app"). Official post not found by me.
  [Chinese-language overview](https://vantaige.io/zh/ai-tool/open-interpreter)
- Why (reported, not verified): hardware people already own does the job better.
- Same pattern as Rabbit and Humane: the software idea survived, the gadget did not.

---

## B. Big-vendor OS integration

### Microsoft Recall (Copilot+ PCs)
- Tried: screenshot your screen every few seconds and make it searchable.
- Backlash: researchers showed the local database was readable by malware.
  A tool called TotalRecall demonstrated the bypass.
  [Wikipedia: Windows Recall](https://en.wikipedia.org/wiki/Windows_Recall),
  [SC Media](https://scmagazine.com/news/microsofts-ai-recall-feature-postponed-to-address-security-concerns)
- Outcome: delayed on 14 June 2024. Reworked version: opt-in, encrypted database,
  Windows Hello required each time you open it, uninstall option.
  [Wikipedia: Windows Recall](https://en.wikipedia.org/wiki/Windows_Recall),
  [Dark Reading](https://darkreading.com/endpoint-security/microsoft-releases-recall-windows-insider-preview)
- Third-party reaction: Signal turned on a default screen-security feature that hides
  its window from Recall, citing Recall as the reason (May 2025). Brave and AdGuard
  added blocking in July 2025.
  [Wikipedia: Windows Recall](https://en.wikipedia.org/wiki/Windows_Recall)
- Why: always-on capture of everything, on by default, with weak storage protection.
  Local storage alone did not earn trust.
  GUESS: the same feature opt-in and encrypted from day one would have drawn far less anger.

### Windows "agentic OS", Agent Workspace, MCP on Windows
- Tried: at Ignite 2025 Microsoft put agents at the centre of Windows. An "agent
  workspace" (preview) is a separate desktop where agents act without using your
  session. It stated security principles for MCP on Windows: separate agent accounts,
  limited privileges, signed and revocable trust, privacy-preserving design.
  [Windows blog](https://blogs.windows.com/windowsexperience/2025/11/18/preparing-for-whats-next-windows-security-and-resiliency-innovations-help-organizations-mitigate-risks-recover-faster-and-prepare-for-the-era-of-ai/),
  [Windows blog: Ignite](https://blogs.windows.com/windowsexperience/2025/11/18/ignite-2025-windows-at-the-frontier-of-work/)
- Outcome: users pushed back on the "agentic OS" framing. I could not get detail on
  the specific objections.
  [Petri](https://petri.com/podcast/agent-365-vs-agentic-os-backlash-what-microsoft-got-right-and-wrong-at-ignite/)
- What is useful for Exo: the design pattern (own account, own desktop, scoped
  permissions) is the right shape, whatever the reception.

### Apple Intelligence and Siri
- Tried: a more personal Siri that uses your own data and acts across apps.
- Outcome: in March 2025 Apple told Daring Fireball the features would take longer
  than expected. Gruber said they were never shown working and called them vaporware.
  [Fast Company](https://www.fastcompany.com/91297126/apple-iphone-siri-delay),
  [Daring Fireball excerpt](https://pxlnv.com/linklog/rotten-in-cupertino/)
- Why: commentators offered security or privacy. Gruber doubted that and suggested
  the features may simply not have worked. Unresolved. I could not verify the later
  status.
- Lesson: marketing a personal agent before it works costs trust. (GUESS: the
  hardest part is reliable action on personal data, not the chat.)

### Google Gemini on Android and ChromeOS
- Tried: replace Google Assistant with Gemini as the default assistant.
- Outcome: ChromeOS 134 (March 2025) made Gemini the default on Chromebooks. Voice
  wake was not yet supported at that point. Reports say Assistant branding ends on
  phones after March 2026; I could not confirm that.
  [Android Central](https://androidcentral.com/chromebooks-laptops/gemini-is-taking-over-chromebooks-leaving-google-assistant-in-the-dust),
  [SamMobile](https://www.sammobile.com/news/google-assistant-soon-disappear-completely-galaxy-devices/)
- Why it matters: a swap of the assistant layer inside an existing OS is the
  low-risk route. The old assistant's reliable small tasks were at risk of regressing.
  That second point is GUESS.

---

## C. Agents that operate a computer

### Anthropic computer use
- Tried: a model that looks at screenshots, moves the cursor, clicks, types.
- Outcome: public beta on 22 Oct 2024. Anthropic called it "still experimental,
  at times cumbersome and error-prone", and noted scrolling, dragging and zooming
  were hard. OSWorld: 14.9% screenshot-only, 22.0% with more steps, versus 7.8% for
  the next system.
  [Anthropic](https://www.anthropic.com/news/3-5-models-and-computer-use)
- Later: Anthropic says its Sonnet models went from under 15% to 72.5% on OSWorld
  with Sonnet 4.6 (company claim, Feb 2026 acquisition post).
  [Anthropic: Vercept](https://anthropic.com/news/acquires-vercept)
- Why it matters: pixel-level control improved fast but started weak. Do not build
  a system that only works if screen control is reliable.

### Claude Code
- Tried: a terminal agent that reads, edits and runs code with a permission system
  and an OS sandbox (bubblewrap on Linux, Seatbelt on macOS). The sandbox covers
  Bash only.
  [Third-party summary of docs](https://inventivehq.com/knowledge-base/claude/how-to-manage-permissions-and-sandboxing)
- Evidence on approval prompts: Anthropic says users approve 93% of permission
  prompts, so prompts lose meaning. Sandboxing is "safe but high-maintenance." Its
  "auto mode" uses a prompt-injection probe on tool output and a classifier that
  reviews each action.
  [Anthropic engineering](https://anthropic.com/engineering/claude-code-auto-mode)
- A security vendor argues auto mode has the agent approving itself. Vendor
  opinion, not neutral.
  [grith](https://grith.ai/blog/claude-code-auto-mode-vs-grith)
- Why it matters: this is the closest working example of an agent living on a
  real machine. It supports P3 (rubber-stamp queues fail) and P11 (gate by risk).

### OpenAI Operator, ChatGPT agent, Atlas
- Tried: Operator (Jan 2025, $200/month Pro tier) used a Computer-Using Agent
  model in its own browser and often asked for confirmation.
  [OpenAI](https://openai.com/blog/introducing-operator),
  [Simon Willison](https://simonwillison.net/2025/Jan/23/introducing-operator/)
- Outcome: OpenAI later folded Operator into ChatGPT agent and said the standalone
  site would sunset.
  [OpenAI](https://openai.com/blog/introducing-operator)
- Atlas browser (Oct 2025) offered Agent Mode in preview, with limits: no code
  execution, no file downloads, pauses on sensitive sites. OpenAI warned of
  prompt injection. Users reportedly showed injection examples within hours.
  [National CIO Review](https://nationalcioreview.com/articles-insights/extra-bytes/chatgpt-atlas-raises-alarm-over-new-ai-browser-security-risks/),
  [Pure AI](https://pureai.com/articles/2025/10/21/openai-launches-chatgpt-atlas.aspx)
- Why: the browser reads untrusted pages and holds your logins. That mix is the risk.

### Perplexity Comet
- Tried: an AI browser that can act on pages for you.
- Outcome: Brave found that asking Comet to summarize a page passed page text to
  the model with no separation from the user's request. Their example chained
  a Reddit post, an account-recovery flow and a logged-in Gmail session. Brave said
  Perplexity's fixes were later defeated.
  [Simon Willison, citing Brave](https://simonwillison.net/2025/Aug/25/agentic-browser-security/)
- Willison's view: instructions and untrusted content are one token stream, and
  no one has shown a convincing way to separate them. He points to the CaMeL paper as
  a credible approach. Same page. Perplexity's side of the later claims: not checked.
- Why it matters: direct support for P10.

---

## D. Research systems

### AIOS (Rutgers)
- Tried: an "LLM agent operating system": a kernel that handles scheduling,
  context management, memory, storage and access control for agents.
  [arXiv 2403.16971](https://arxiv.org/html/2403.16971v2)
- Outcome: the paper reports up to 2.1x faster execution when serving agents built
  with various frameworks. Open source at agiresearch/AIOS.
  [Moonlight review of the paper](https://www.themoonlight.io/review/aios-llm-agent-operating-system)
- Why it matters: it is a resource-management view (scheduling, contention). It says
  little about trust, memory by user, or reversibility. It is a useful reference for
  the scheduler layer. Not a user-facing OS.

### OS-Copilot and FRIDAY
- Tried: a framework for a generalist agent over the whole OS (web, terminal, files,
  apps) that learns to use unfamiliar apps via self-directed practice and saves
  what it learned. ICLR 2024.
  [arXiv 2402.07456](https://arxiv.org/abs/2402.07456v2)
- Outcome: authors report FRIDAY beats prior methods on GAIA by 35% and learns Excel
  and PowerPoint with little supervision. Self-reported; I did not check.
  Same page.
- Why it matters: "save a working procedure as a reusable skill" is the same idea
  later seen in Hermes Agent.

### MemGPT and Letta
- Tried: treat LLM context like OS memory. Move data between fast context and slow
  storage using function calls, so an agent has long-term memory and handles
  documents bigger than its context.
  [arXiv 2310.08560](https://arxiv.org/pdf/2310.08560)
- Outcome: tested on document analysis and multi-session chat. I did not verify how
  the project relates to Letta; check before citing.
- Why it matters: supports P7 (two-tier memory; long context is not memory).

---

## E. Personal agent harnesses

### OpenClaw (formerly Clawdbot, Moltbot)
- Tried: a viral personal agent with plugin "skills" from a public hub (ClawHub)
  that the agent can search for and install on its own. Renamed on 29 Jan 2026.
  [eSecurity Planet](https://www.esecurityplanet.com/threats/openclaws-rapid-rise-exposes-thousands-of-ai-agents-to-the-public-internet/),
  [TechRadar](https://www.techradar.com/pro/here-are-the-openclaw-security-risks-you-should-know-about)
- Outcome: Censys counted over 21,000 publicly exposed instances by 31 Jan 2026.
  Other counts are much higher and disagree (SecurityScorecard 42,900, then rising
  past 220,000). Treat counts as unreliable.
  [eSecurity Planet](https://www.esecurityplanet.com/threats/openclaws-rapid-rise-exposes-thousands-of-ai-agents-to-the-public-internet/),
  [Hackmag](https://hackmag.com/news/openclaw-open)
- Skills: Cisco examined a malicious skill that used prompt injection and sent data
  out. Counts of malicious skills range from 341 to 824+ across sources.
  A named CVE: CVE-2026-24763, a Docker sandbox escape, patched.
  [TechRadar](https://www.techradar.com/pro/here-are-the-openclaw-security-risks-you-should-know-about),
  [Securonix](https://connect.securonix.com/threat-research-intelligence-62/openclaw-threat-intelligence-report-clawdbot-moltbot-and-openclaw-208)
- Why: easy install, network exposure by default for many, an unvetted skill store,
  and an agent with real power. Popular does not mean safe.

### Hermes Agent (Nous Research)
- Tried: open source agent that writes skills from experience, improves them,
  keeps persistent memory and a model of the user across sessions. MIT licence.
  [Project docs](https://hermes-agent.nousresearch.com/docs)
- Memory design per third-party write-ups: SQLite session history, full-text search,
  layered notes. Details differ between write-ups.
  [Turing Post](https://www.turingpost.com/p/hermes),
  [Fast.io](https://fast.io/resources/hermes-ai.md)
- Adoption figure (152,000 GitHub stars by May 2026) is one third-party source.
  Unverified.
- Why it matters: it is the nearest design neighbour to Exo's skills and memory
  loop. I did not test its safety record. GUESS: a self-writing skill loop needs
  the review step in P9, or it drifts.

### Home Assistant voice (Assist)
- Tried: a fully local voice assistant. Stack: wake word, speech-to-text,
  conversation agent (fixed intents or a local LLM via Ollama), text-to-speech,
  joined by the Wyoming protocol. Each part can run on separate machines.
  [InsiderLLM guide](https://insiderllm.com/guides/home-assistant-local-llm-guide/)
- Tradeoffs: Speech-to-Phrase is fast but limited to set phrases. Whisper handles
  open speech but took about 8 seconds on a Pi 4 versus under 1 second on a NUC
  or GPU (same guide, not independently measured).
  One build runs fully offline on a Pi with Whisper, Piper and a 4B model.
  [DEV Community build](https://dev.to/tsukiyo/how-i-built-a-budget-local-home-assistant-voice-assistant-no-nabu-casa-no-cloud-mej)
- Why it matters: it shows a local-first agent can work when the action set is
  narrow (device commands) and each piece is swappable. I found no official
  Home Assistant page in this pass.

### Jolla (Sailfish OS, Mind2)
- Tried: a European phone OS vendor adding a "privacy-first AI computer" (Mind2)
  with a vector database, local LLMs and decentralised identity. Cloud models
  are optional and the interface flags when data leaves the device.
  [Jolla press release](https://jolla.com/content/uploads/2024/05/Jolla-Unveils-Mind2-AI-Computer-and-Community-Phone-and-Partnership-with-Reeder-May202024.pdf?x44624),
  [TechSpot](https://www.techspot.com/news/111540-jolla-sailfish-pitches-european-phone-users-wary-google.html)
- Outcome: Mind2 is not standalone; it needs a phone or computer.
  [Hackster](https://hackster.io/news/jolla-partners-with-venho-ai-on-the-mind2-a-privacy-first-ai-computer-accessory-for-your-phone-d02aaffabb61)
  The new Jolla phone ships without built-in AI at launch, with partial Mind2
  integration promised later. Which features shipped: unverified.
  [TechSpot](https://www.techspot.com/news/111540-jolla-sailfish-pitches-european-phone-users-wary-google.html)
- Why it matters: "flag when data leaves the device" is a small UX rule worth copying.

---

## F. Desktop communities and AI

### GNOME, KDE, COSMIC
- I did not find a shipped GNOME or KDE agent feature. What I found is policy debate.
  GNOME Extensions prohibits AI-generated extensions. A GNOME developer proposed
  barring LLM-made contributions. KDE's maintainer drafted a permissive "Don't be
  lazy" policy; a group asked for a ban and about 250 people signed. System76's
  COSMIC requires contributors to certify no LLM-generated content in pull requests.
  [The Register](https://www.theregister.com/a/5298854),
  [Linux Magazine](https://www.linux-magazine.com/Online/News/KDE-for-People-Initiative-Calls-for-AI-Ban-in-Plasma),
  [Let's Data Science on COSMIC](https://letsdatascience.com/news/cosmic-bans-ai-generated-pull-request-content-df1a405a)
- These reports date from about September to October 2026 and may have changed.
- Why it matters: the Linux desktop community is wary of AI. An AI layer on Linux
  should be an add-on, optional, off by default, and easy to remove. (GUESS.)

---

## G. Older ideas worth remembering

These are history, so I rely on summaries. Lessons are my reading (GUESS unless stated).

- Xerox Alto and Smalltalk: the Alto introduced the windows, icons, menus and mouse
  model, and Kay's group added overlapping windows and popup menus. The Xerox Star
  (1981) sold poorly; sources disagree on price and cause. Apple's Lisa failed on price
  and performance, per the Computer History Museum.
  [CHM: GUI](https://www.computerhistory.org/revolution/input-output/14/348),
  [CHM: Lisa](https://computerhistory.org/?p=26613)
  Lesson: a great environment still has to be cheap and fit the buyer.
- Plan 9: "everything is a file", one protocol (9P) to reach remote resources.
  It did not displace Unix. Cited reasons: incompatible with existing software,
  late, and priced as a product. (The price is an unverified forum claim.)
  Ideas like UTF-8 and /proc moved into other systems.
  [The Register](https://www.theregister.com/2024/02/21/successor_to_unix_plan_9/),
  [Carleton course notes](https://homeostasis.scs.carleton.ca/wiki/index.php/DistOS_2021F_2021-09-28)
  Lesson: uniform file-like interfaces suit agents (plain text, greppable,
  scriptable). Do not require users to leave their software.
- Emacs as an OS: GNU Emacs is written mostly in Emacs Lisp, so almost anything is
  extensible. The "OS missing a decent editor" joke is old but its source is uncited.
  [Wikipedia: Emacs](https://en.wikipedia.org/wiki/Emacs),
  [Wikipedia: Editor war](https://en.wikipedia.org/wiki/Editor_war)
  Lesson: a live, inspectable, scriptable environment built around one user
  beats a locked shell. Agents work well here because everything is text.
- Genera / Lisp machines: Symbolics and LMI built whole machines around Lisp.
  Accounts of the collapse differ: commodity workstations sped up faster than
  custom hardware, small volumes, and bad business choices. Symbolics went
  bankrupt in 1996 and later ran Genera on Alpha.
  [Dan Luu](https://danluu.com/symbolics-lisp-machines/),
  [Wikipedia: Lisp machine](https://en.wikipedia.org/wiki/Lisp_machine)
  Lesson: do not tie the idea to custom hardware. Run on commodity machines.
- Sugar (OLPC): not researched in this pass. No claim made.

---

## Distilled lessons for Exo OS

1. **Be an add-on layer on a normal OS, not a new device or a new kernel.**
   Humane, Rabbit and the 01 Light all lost to "it should have been an app," and
   Plan 9, Genera and Star lost on compatibility or hardware cost. Exo already
   sits on Omarchy and Linux. Keep it that way. (A, G above.)

2. **Own the data and keep it portable, so the product outlives the vendor.**
   Humane pins stopped working when servers stopped. Keep the log and memory local
   and exportable, as in P1 and P18. Cloud models can be swapped; the store cannot
   disappear. (A, P1, P18.)

3. **Privacy by default: opt-in, encrypted, scoped, and visible.**
   Recall lost trust through default-on capture and weak storage, and third-party apps
   began blocking it. Exo should capture only what A chose, encrypt it at rest, and
   show where data leaves the machine (Jolla's flag). Avoid screen-capture-everything.
   (B, E.)

4. **Treat all external content as untrusted data.**
   Comet, Atlas and OpenClaw were all hit by injection or hostile skills. No one has a
   proven fix at the model level, so rely on structure: separate channels, least
   privilege, no secrets and no outbound sends in the same context that reads the web.
   This is P10. (C, E.)

5. **Gate by reversibility, not by prompt count.**
   93% of Claude Code prompts are approved, so blanket prompts are noise. Let
   reversible, private actions run, logged and undoable. Require a real decision for
   irreversible, public, financial or third-party actions. This is P3 and P11. (C.)

6. **Run agents in their own account or workspace with scoped power.**
   Windows' agent workspace and Claude Code's sandbox point the same way: a separate
   identity, a limited file area, signed and revocable permissions. Exo should do
   this for every agent seat. (B, C.)

7. **Prefer structured tools and text over pixel control.**
   Screen control began at 14.9% on OSWorld and brittle per-site scripts sank Rabbit's
   claims (reported, not proven). Use APIs, files and MCP-style connectors first;
   use screen control as a fallback. (A, C, G Plan 9 and Emacs.)

8. **Memory is the real product, and it needs structure and review.**
   MemGPT, Hermes and OS-Copilot all converge on tiered memory plus saved skills.
   The risk is a self-edited store that drifts or leaks. Keep facts with sources,
   keep skill changes as reviewable diffs (P7, P9). (D, E.)

9. **Vet the supply chain: skills and plugins are code.**
    OpenClaw's skill store carried malware, and one audit reportedly found 26% of 31,000
    skills had a vulnerability (one secondary source, no link kept). Exo skills should be signed,
    pinned, local by default, and read before install. (E.)

10. **Keep the action set narrow and each part swappable (local first).**
    Home Assistant works locally because the job is narrow and parts are replaceable;
    cost and latency are tuned per part (8 seconds versus under 1 second for the same
    speech step on different hardware, per one guide). Route cheap work to local
    models and spend frontier tokens only where it compounds (P17 and the cost
    resolution in PRINCIPLES.md). (E.)

## Open questions
- Confirm the Rabbit "Playwright scripts" claim from a primary source.
- Check Siri and Gemini status after March 2025 and March 2026.
- Check OpenClaw counts against Censys and SecurityScorecard originals.
- Read Hermes Agent source for its safety model.
- Sugar (OLPC) and Plan 9 primary sources: not done.
