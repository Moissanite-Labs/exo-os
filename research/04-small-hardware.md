# 04. What runs on a small machine for Exo OS

Date: 2026-10-12. Status: research note, not a decision.

How to read this note:
- "Source" means a published number with a link. Many are blog posts, not lab tests. I say so.
- "GUESS" means my estimate. "Unknown" means I found no number.
- I ran no benchmarks. The only local commands were `lscpu` and `free -m`.

## 1. The test machine

Read with `lscpu` and `free -m` (read-only):

| Item | Value |
|---|---|
| CPU | AMD Athlon Silver 3050U, x86_64 |
| Cores / threads | 2 cores, 1 thread each (2 CPUs) |
| Clock | 1.4 GHz min, 3.2 GHz max |
| RAM | 5853 MB total (the 5.7 GB) |
| In use at the time | 2447 MB used, 2685 MB cache, 3405 MB available |
| Swap | 11705 MB |

Notes:
- The used figure already includes a running desktop and other apps. This is a live machine, not a clean idle boot.
- Only 2 threads is the real limit. Local models get slow before RAM runs out.
- Swap on disk is a safety net, not a place to run models. A model that swaps is unusable.

## 2. Desktop RAM: Wayland compositor plus Chromium

Source figures are weak. No lab comparison of idle compositor RAM was found.

- Hyprland: one user reports about 200 MB idle with blur, shadows and animations off ([moddedbear.com](https://moddedbear.com/trying-hyprland/)). Another says just under 200 MB ([Gentoo forum](https://forums.gentoo.org/viewtopic-p-8843698.html)).
- sway: a forum user says vanilla sway uses 50 MB, but it may mean video memory, not RAM ([EndeavourOS forum](https://forum.endeavouros.com/t/share-your-desktop-2025/69504?page=75)).
- niri: unknown. No measured MB found.
- Cosmic: unknown. No measured MB found.
- GUESS: sway is lightest, then niri, then Hyprland with effects off, then Cosmic (a larger shell). Treat as order only, not numbers.
- Shell extras (bar, notifications, launcher, lock) add tens of MB each. GUESS: 100 to 300 MB total for a tidy tiling setup.

Chromium is the real cost, not the compositor:
- A 2026 vendor post measured about 76 MB for an idle Wikipedia tab, about 110 MB for Hacker News, and 470 to 530 MB for 1080p YouTube. It is the vendor's own test ([superchargebrowser.com](https://www.superchargebrowser.com/library/chrome-ram-usage-per-tab-2026/)).
- Another post says heavy web apps (Gmail, Notion, Slack) often hold 200 to 500 MB per tab, with no named benchmark ([supasidebar.com](https://supasidebar.com/blog/reduce-chrome-memory-usage-tabs-2026)).
- Chromium also has fixed cost: browser, GPU and network processes. GUESS: 300 to 500 MB before any tab.
- Memory Saver freezes unused tabs ([ghacks.net](https://www.ghacks.net/?p=204464)).

Budget for a 5.7 GB machine (GUESS, to be measured):

| Part | MB |
|---|---|
| Kernel, systemd, services | 300 |
| Compositor and shell | 300 |
| Chromium with 3 to 5 tabs (WhatsApp, Gmail, Drive, Meet, CRM) | 1500 to 2500 |
| `exod` | 150 to 400 (see section 9) |
| Left for local models | 1000 to 2000 |

This matches the 1 to 2 GB window in the brief. It leaves nothing if Meet and Chromium both run hot.

## 3. Local LLMs that fit in 1 to 2 GB

Rule of thumb (well known, GUESS on exact numbers): a model at 4-bit needs roughly 0.55 to 0.65 GB per billion parameters, plus the KV cache (grows with context) plus runtime overhead.

Candidates:
- Qwen2.5 0.5B / 1.5B, Qwen3 0.6B.
- Gemma 3 270M. It has 270M parameters, 170M of them in the word table. Google ships INT4 QAT checkpoints. Google reports 0.75% battery for 25 chats on a Pixel 9 Pro, a vendor number ([habr summary](https://habr.com/en/news/937456)). No RAM figure from the model card found: unknown.
- SmolLM2 135M / 360M / 1.7B and Phi-3.5-mini: no numbers found in this pass. Unknown.
- TinyLlama 1.1B: old, weaker. Only useful as a benchmark reference.

Published speeds (all on other hardware, none on a 3050U):

| Model, quant | Device | Result | Source |
|---|---|---|---|
| Qwen2.5 1.5B Q4_K_M | Raspberry Pi 5, Ollama | 9.97 tok/s, 1849 MB RAM | [Stratosphere study](https://stratospherelinuxips.readthedocs.io/en/develop/immune/research_rpi_llm_performance.html) |
| TinyLlama 1.1B Q4_0 | Raspberry Pi 5, llama.cpp | 14.4 tok/s | [tinyweights.dev](https://tinyweights.dev/posts/run-llms-raspberry-pi-5/) |
| Qwen2.5 1.5B Q4_K_M | RK3588 (Turing Pi RK1), llama.cpp, 4 threads | 22.57 tok/s | [Turing Pi](https://turingpi.com/llm-inference-benchmarks-rk3588-gguf-quantization/) |
| Llama 3.2 3B | RK3588 | 11.25 tok/s | same |
| TinyLlama 1.1B | RK3588S NPU (RKLLM), Radxa figure | 15.03 tok/s | [tinycomputers.io](https://tinycomputers.io/posts/rk3588-orange-pi-5-max-review.md) |

What this means for the test machine:
- The Pi 5 has 4 cores. The 3050U has 2 Zen+ cores at up to 3.2 GHz. GUESS: a 1.5B Q4 model gives about 3 to 8 tok/s here. Must be measured. Token generation is limited by memory bandwidth, and laptop single-channel DDR4 is not fast.
- Prompt reading (prefill) is slower than generation on weak CPUs. Long prompts feel slow. Not measured here: unknown.
- Thermals matter. One guide says a Pi 5 without a fan throttles within 90 seconds and halves its speed (same tinyweights source, a guide not a test).
- Quantization: Q4_K_M is the common balance. Q8 doubles RAM for small quality gain. Below Q4 quality drops fast on tiny models (general knowledge, not sourced here).
- llama.cpp vs Ollama: the same guide reports close generation speed (14.4 vs 13.75 tok/s). Ollama adds a daemon and some RAM. GUESS: use llama.cpp's `llama-server` directly on small machines.

Fit verdict (GUESS): on 5.7 GB with a browser open, run one model of 1.5B or smaller at Q4, with context 2048 to 4096. Load on demand and unload after idle.

## 4. Embedding models

- all-MiniLM-L6-v2: about 22.7M parameters, 384 dimensions, 256 token input, Apache 2.0 ([superlinked glossary](https://superlinked.com/glossary/what-is-all-minilm-l6-v2)). One source says about 80 MB on disk ([promptlayer](https://www.promptlayer.com/models/all-minilm-l6-v2)). Another says 42 MB but also gives the wrong dimension, so distrust it. Check the Hugging Face card.
- bge-small-en-v1.5: about 33M parameters, 384 dimensions, English focused ([reference table](https://ink-termite-00a.notion.site/RAG-Reference-Metrics-1d083ecdfdac8078926eef18c8ceee5c)).
- EmbeddingGemma (308M): no source found in this pass. Unknown.
- GUESS: both small models run in under 300 MB RAM and embed a short note in tens of milliseconds on this CPU. Not measured.
- Multilingual need: the owner uses Arabic and Urdu keyboards. The two models above are English focused. A multilingual small embedder is needed and was not researched here. Open item.

## 5. Voice

Speech to text:
- whisper.cpp memory, from a fork README: tiny about 390 MB, base about 500 MB, small about 1.0 GB, medium about 2.6 GB ([kardianos/whisper.cpp](https://github.com/kardianos/whisper.cpp)). A secondary post gives lower: tiny 273, base 388, small 852 MB ([openwhispr](https://openwhispr.com/blog/whisper-model-sizes-explained)). The sources disagree. Use the higher numbers for planning.
- faster-whisper project table, small model, 13 min of audio, i7-12700K 8 threads: openai/whisper fp32 6m58s and 2335 MB; faster-whisper int8 1m42s and 1477 MB; whisper.cpp fp32 2m05s and 1049 MB ([faster-whisper on PyPI](https://pypi.org/project/faster-whisper/)). Project numbers, not independent. A desktop CPU, far faster than the 3050U.
- GUESS: on 2 weak cores, `base` or `tiny` is the only practical live size. `small` is likely slower than real time. Must be measured.
- Accuracy for Arabic and Urdu on tiny and base is poor (GUESS from general knowledge). Remote speech to text is better for those languages.

Text to speech:
- Piper: voices are about 27 to 115 MB each, one source says about 63 MB for a medium voice. Reports say real time on a Pi 4 or 5; one gives a real-time factor near 0.19 on unnamed hardware. These are SEO-style blog posts ([aivideosensei](https://aivideosensei.com/guides/piper-tts-offline-voice-guide), [promptquorum](https://www.promptquorum.com/power-local-llm/best-local-tts-raspberry-pi)). Treat as rough.
- Kokoro-82M: 82M parameters. Download size is reported as 88 to 92 MB int8 ONNX by some and about 327 MB by others. Speed claims: about real time on a modern CPU, versus Piper about 9 times real time, from one comparison ([voicechanger.live](https://voicechanger.live/text-to-speech/compare/kokoro-vs-piper)). No solid Pi or low-power benchmark: unknown.
- GUESS: Piper for local fallback on this machine. Kokoro only on a faster tier.

## 6. Small classifiers for decisions

Many "decisions" in `exod` do not need an LLM:
- Route: is this a local task, a remote task, or a command? A tiny classifier or embedding plus nearest match can do it. GUESS: under 100 MB, under 50 ms.
- Wake word, intent, yes/no, spam, "needs approval" gates: small models or rules.
- A 270M to 600M LLM (Gemma 3 270M, Qwen3 0.6B) can be fine tuned for narrow tasks. Google positions Gemma 3 270M for this use ([habr summary](https://habr.com/en/news/937456)).
- Rule: any decision that can break something (spend money, send mail, delete) should be approved by code rules or the user, not by a tiny model alone.

## 7. What must stay remote

Reasoning and quality:
- Multi-step planning, coding, tool-use chains, long documents, anything where a wrong answer costs time.
- Long context. KV cache grows with context, so 32k tokens locally is out of reach in 1 to 2 GB. Prefill time on 2 cores would also be very slow (GUESS).
- Arabic and Urdu quality at small sizes (GUESS).
- Vision and large audio work.

Local is for: private, fast, tiny, or offline jobs (section 10).

## 8. Battery and thermals for a portable companion

Published figures are limited. Do not trust any mW number without a test.

- Raspberry Pi 5: about 3 W idle headless over Wi-Fi, about 3.6 W with Ethernet, HDMI and USB; about 8.8 W with all cores loaded; up to about 16 W in an extreme case. Source is a blog ([raspberry.tips](https://raspberry.tips/en/?p=9083)). A forum user reported about 4 W idle ([Pimoroni forum](https://forums.pimoroni.com/t/pi-5-power-consumption/23959)). The Hailo-8L HAT adds 2 to 3 W while inferring, per a vendor-style guide ([fast.io](https://fast.io/resources/openclaw-raspberry-pi-power-supply-always-on-agent-guide/)).
- Jetson Orin Nano Super: 25 W power mode confirmed in listings ([DFRobot](https://www.dfrobot.com/product-2900.html)). A lower mode exists in NVIDIA docs (GUESS, not confirmed here).
- Test laptop: the 3050U has a configurable TDP of 15 W class (GUESS from knowledge, not checked in this pass).
- Design rule (GUESS): the companion should be idle most of the time. Model inference in bursts. Remote calls cost radio power too.
- Thermals: sustained local inference throttles small fanless boxes. Cap threads, cap duty cycle, and prefer short jobs.
- Voice wake loop: keep the always-on part tiny (wake word, VAD), under 1 W if possible (GUESS).

## 9. Sizing `exod` memory

`exod` is Python and calls remote models by API. That means it is mostly I/O.

GUESS table, to be measured with `smem` or `/proc/PID/status` on the real build:

| Part | MB |
|---|---|
| Python interpreter and core libs | 30 to 60 |
| HTTP client, async loop, JSON | 20 to 50 |
| Memory store (SQLite, small vector index) | 20 to 100 |
| Tool plugins and MCP adapters | 20 to 100 |
| Local embedder, if loaded in-process | 100 to 300 |
| Total, no local models | about 100 to 250 |
| Total, with embedder and classifier | about 300 to 500 |

Advice:
- Run local models as separate processes (`llama-server`, whisper, Piper), not inside `exod`. Then they can be started, capped (systemd `MemoryMax`), and killed without touching the core.
- Set a memory budget per tier and make `exod` check `MemAvailable` before loading a model.
- Avoid heavy Python imports at start (numpy, torch). Torch alone is hundreds of MB (general knowledge). Prefer ONNX Runtime or llama.cpp bindings.
- Stream and trim context. Remote calls with long history cost money and time, not local RAM, but the in-memory copy still counts.
- Test target: `exod` plus Chromium plus compositor must leave at least 1 GB free on the 5.7 GB machine.

## 10. Offline mode and home server sync

- Offline: the device keeps working for simple things. Local small model for chat, dictation, notes, search over local embeddings, queue of outgoing tasks.
- Queue and replay: remote calls that need big models wait in a local queue (SQLite) and run when the link returns. Show the user clearly what is queued.
- Home server (GUESS design): a mini PC or NAS runs the larger local models, the shared memory store, and a relay to remote APIs. The device syncs notes, embeddings and queue with it over the private network.
- Sync rules: one writer per record, append-only logs where possible, timestamps plus device id for conflicts, and nothing secret in the sync unless encrypted.
- API keys: keep them on the home server where possible, so a lost device does not leak them.
- The home server can also run bigger voice models (Whisper small or medium, Kokoro) and serve them to the thin device.

## 11. Candidate devices

All prices and specs are from the linked pages unless marked GUESS.

- Raspberry Pi 5: 4 cores, models above show about 10 tok/s for a 1.5B Q4. Idle about 3 W. Needs a fan for sustained loads.
- Pi 5 with AI HAT+ 2 (Hailo-10H): 40 TOPS INT4, 8 GB on-board RAM separate from the host, $200, runs LLM and vision models from Hailo's repo ([raspberrypi.com](https://www.raspberrypi.com/products/ai-hat-plus-2/), [TechRadar](https://www.techradar.com/pro/want-some-ai-with-your-pi-raspberry-pi-5-gets-an-llm-upgrade-with-new-ai-hat-2-but-will-it-be-powerful-enough)). The 8 GB is the main gain. LLM speed on it: unknown, no figure found. Model support is limited to Hailo's list.
- Orange Pi 5 and other RK3588 boards: strongest CPU in the cheap ARM class from the figures above (22.57 tok/s for Qwen2.5 1.5B). NPU path (RKLLM) needs Rockchip converted models; Radxa quotes 15 tok/s TinyLlama, 6.46 tok/s Phi3 3.8B ([cnx-software](https://www.cnx-software.com/?p=133724)). Linux support is vendor kernels (GUESS: check before buying).
- Jetson Orin Nano Super: $249, up to 67 INT8 TOPS, 8 GB shared memory, 25 W mode ([DFRobot](https://www.dfrobot.com/product-2900.html)). The best local GPU option in this list. Too much power for a pocket device. Retail prices vary a lot, some listings are far above $249.
- Snapdragon X (Elite/Plus) laptops: Qualcomm quotes about 10 tok/s for an 8B model at w4a16 via its own runtime ([Hugging Face card](https://huggingface.co/qualcomm/Llama-SEA-LION-v3.5-8B-R/commit/2da61f0d0a8714782d5e7364f2e27f47a3b6b35a)). Third-party blog figures are unsourced. Linux support status: unknown, not checked. The NPU needs vendor software.
- Framework Laptop 12 (2026): Intel Core 3/5/7 series 3, 15 W class, 8 to 32 GB RAM, 12.2 inch, from USD 699, ships with Fedora 44 or Windows ([cnx-software](https://www.cnx-software.com/2026/08/23/framework-laptop-12-repairable-convertible-laptop-gets-wildcat-lake-motherboard-ships-with-fedora-44-or-windows-11/)). Repairable and good Linux support. Good tier 2 host. Local LLM speed: unknown.
- Steam Deck class (AMD APU handheld): not sourced in this pass. GUESS: 16 GB RAM and a GPU make it a strong x86 local-model box, but it is a gaming device with a locked-down store. Unknown for Exo OS.
- Old ThinkPad (x86, 8 to 16 GB): not sourced. GUESS: cheap, good Linux, upgradeable RAM. Often the best value for a tier 1 or 2 test.

## 12. Device tiers

| Tier | Example hardware | Runs locally | Remote |
|---|---|---|---|
| 0. Thin | Pi Zero 2 / small ARM board, under 2 GB RAM | `exod` core, wake word, VAD, rules, queue, tiny classifier | All LLM, speech to text, text to speech |
| 1. Small (the test machine) | Athlon Silver 3050U laptop, 5.7 GB; Pi 5 4 to 8 GB; old ThinkPad 8 GB | `exod`, desktop and Chromium, one 0.3 to 1.5B Q4 model on demand, small embedder, Whisper tiny or base, Piper | Reasoning, long context, Arabic and Urdu speech, vision |
| 2. Mid | Framework 12 with 16 GB, RK3588 with 16 GB, Pi 5 plus Hailo HAT | Tier 1, plus a 3B to 4B model, Whisper small, Kokoro (GUESS) | Hard reasoning, long context, large vision |
| 3. Home box | Mini PC or NAS with 32 GB, or Jetson Orin Nano Super 8 GB | 7B to 8B class models, Whisper medium, shared memory and sync for all devices | Frontier reasoning, very long context |
| 4. Cloud | Hosted model APIs | Nothing local | Big reasoning, long context, everything hard |

Notes on the table: tier 2 and 3 model sizes are GUESS and depend on measured speed. Tier 1 is the target for the first release.

## 13. Recommendations

1. Measure first. Run `llama-bench`, whisper.cpp bench and Piper on the 3050U machine with Chromium open, and record tok/s, RAM and temperature. Replace every GUESS above with a real number.
2. Design `exod` so local models are optional, separate processes with a memory cap.
3. Pick one default local model of about 1.5B Q4 for tier 1 (for example Qwen2.5 1.5B), load on demand, unload on idle.
4. Use remote models for anything that matters. Use local models for privacy, offline, and cheap decisions.
5. Test Arabic and Urdu with speech to text and embeddings early. Small English-first models may fail there.
6. Add a home server tier early. It solves heavy local work, key storage and sync in one place.
7. Open questions: Cosmic and niri RAM, Gemma 3 270M and SmolLM2 RAM, Hailo-10H LLM speed, Snapdragon X Linux status, Steam Deck and ThinkPad figures.

## Sources

Main links are inline above. Quality varies: the Stratosphere study, Turing Pi tests and the faster-whisper README are the most method-aware. Many others are blog posts and vendor pages. Check any number before it enters a design.
