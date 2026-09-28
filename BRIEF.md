PRODUCT BRIEF — "CARBONATO-CLASS AGENT INTRUSION: DETECTION TOOL"
Prepared by Lydia Raven Kincaid for Robbie Perdue
For handoff to: Software Developer
Date: Sept 27, 2026

========================================================================
PART 1 — SOURCE MATERIAL (verbatim, Riley's TLDR Information Security,
Sept 25, 2026 — the story that triggered this brief)
========================================================================

## CARBONATO: A Botnet Built Around an AI Agent
https://www.trendmicro.com/en_us/research/26/i/carbonato-ai-agent-botnet.html

CARBONATO worms through unauthenticated Docker daemons on port 2375 using
privileged host-mounted containers, then installs the stock open-source Hermes
Agent and overwrites its SOUL.md persona so Telegram-tasked LLM sessions run
post-exploitation and loot AI API keys before SSH credentials. Trend Micro
recovered the toolkit from the crew's own open registry, which also serves as
the fleet's update server, and ties the operators to Costa Rica via voseo
Spanish, UTC-6 build timestamps, and a +506 Telegram handle.

RILEY'S TAKE (verbatim): Read the details of this one twice, because it's the
most instructive intrusion in this edition and it doesn't have a single novel
exploit. Unauthenticated Docker on 2375 is a fifteen-year-old misconfiguration.
The novelty is entirely in the objective ordering — AI API keys first, SSH
credentials second — and that inversion is the whole story. An attacker who
steals your SSH keys gets your servers. An attacker who steals your API keys
gets your ability to think: inference budget, and the ability to run expensive
workloads on your dime through your own agent infrastructure. Given how much of
a modern company's cost structure and workflow runs through model endpoints,
API keys are now the higher-value credential, and almost no inventory process
treats them that way. The other detail worth a moment: they didn't write a
malware framework, they installed a legitimate agent and overwrote its persona
file. That's a fifteen-second change to a markdown file, and it converts an
innocent productivity tool into a remote-controlled operator. Scan for that,
not for the package name. If you can't grep your fleet for someone else's agent
identity, you don't have agent hygiene.

SUPPORTING SOURCE (same edition, same disease):
## Graphalgo Malware Spreads to Terraform Providers and Go Modules
https://www.aikido.dev/blog/graphalgo-terraform-go-modules
- Trigger conditions hide execution until targeted inputs appear, defeating
 static scanning and sandbox detonation.
- C2 over Slack + an Arbitrum Sepolia smart contract — no server to seize,
 block, or sinkhole. Free, and hosted on systems your business already trusts.
- Defensive implication: you cannot blocklist your way out of a channel the
 business legitimately depends on. Detection, not blocking.

## A Placeholder Domain Referenced in 1,700+ Repos Now Serves Malware
https://sansec.io/research/third-party-com-placeholder-hijack
- A registered fake domain cited across 1,700+ GitHub repos — including AI
 agent skill definitions and MCP documentation — now serves ClickFix lures.
- "Agent skills are the new curl | bash." Installed by the dozen, largely
 unaudited, and the documentation conventions often tell the agent to fetch
 a URL. Audit skill files the way you audit dependencies.

========================================================================
PART 2 — WHY GENERIC SECURITY TOOLING WON'T CATCH THIS
========================================================================
The CARBONATO intrusion has no malicious byte. The attacker installed a
legitimate open-source agent and edited its identity file. The file contents
are markdown; there is no signature to match. Waiting for a named campaign
signature means waiting for the breach to already have happened.

Two assumptions break at once:
 1. EDR/AV assume malware exists as code. Here the "malware" is a config edit
 to a trusted tool.
 2. Existing SIEM tooling assumes human-shaped traffic. An agent pursuing an
 objective looks like legitimate automation until it doesn't.

The gap is real, nameable, and currently unserved: there is no product that
understands what an *agent* looks like at rest and in motion, across a fleet.

========================================================================
PART 3 — PRODUCT CONCEPT
========================================================================
Working name: (placeholder — Rob to pick; suggest "WATCHMAN" or "WARDEN")
One-line: Agent-layer intrusion detection — Snort for AI agents.

WHAT IT IS
A fleet-wide agent security tool with three detection layers, cheapest first.
It ships as a lightweight collector (one binary/container per host or per
orchestrated workload) plus a central rule/alert service.

LAYER 1 — IDENTITY DRIFT DETECTION (the direct CARBONATO counter)
- Baseline a content hash of every agent's identity/persona files, skill
 files, and config (e.g. SOUL.md, persona files, skill definitions, model
 config, tool registrations).
- Any change produces a diff alert, attributed to a host and a user.
- The attacker's 15-second markdown edit becomes a pageable event.
- Must be low-noise: allow approved-change windows, sign-off, or config
 baselines per agent, or operators will tune it off.
Detection value: HIGH. Implementation cost: LOW.

LAYER 2 — PROVENANCE / SUPPLY-CHAIN INTEGRITY
- Every agent file carries a signature: who wrote it, when, from what source.
- Unsigned or unaccounted-for skill/persona files are flagged as untrusted.
- Directly addresses "agent skills are the new curl | bash" and the
 placeholder-domain ecosystem.
- Optional tie-in: a hash-pinned manifest of approved skills per agent.
Detection value: HIGH for supply-chain vectors. Cost: MEDIUM.

LAYER 3 — BEHAVIORAL RULES ENGINE (the actual "Snort for agents")
- This is the differentiator. Rules match on ACTIONS, not files.
- Example rule primitives (initial rule set target: ~20-30 rules):
 * Agent process spawns a shell or interpreter
 * Agent makes outbound calls to a chat service (Slack/Discord/Telegram)
 from a build/CI context, or to a blockchain RPC endpoint
 * Agent reads a credential store it has never touched before (novelty)
 * Agent requests a NEW tool/scope not in its baseline
 * Agent reads a model-endpoint/API-key file (tie to the CARBONATO ordering)
 * Agent's identity file changed within N minutes of an outbound call
 * Agent process survives its parent user session (persistence)
 * Container launched privileged with host mounts (the 2375 entry vector)
- Rules are versioned text files, shareable, community-publishable — the Snort
 rule model. Detection fires on the action; response is alert-only in v1.
Detection value: HIGH, and it is the reason this product exists. Cost: HIGHER
(each rule needs a telemetry source and a test fixture).

WHY ACTION-RULES ARE RIGHT AND FILE-SIGNATURES ALONE ARE WRONG
File signatures are evadable by design (G2 in Part 2). Action rules match
*behavior* the attacker cannot avoid if they want the outcome — you cannot
steal API keys or take C2 over Slack without an action that a rule can name.
This mirrors the Snort lesson exactly: signatures name the *payload*; good
rule engines name the *intent*.

DIFFERENTIATOR (why this is a product and not a script)
- Fleet view: every agent, its baseline, its drift, its rules, in one place.
- Rule ecosystem: shareable, versioned, auditable rule packs. The community
 asset compounds; a one-off scanner does not.
- Positioned against a named, documented technique (CARBONATO) with a live
 adversary and a public toolchain recovered by Trend Micro.

========================================================================
PART 4 — SCOPE (phased)
========================================================================
PHASE 0 — DISCOVERY (3-5 days)
- Enumerate target environments: what runs agents, how they're launched
 (bare host, Docker, K8s), where identity/skill files live, what telemetry
 is already available (auditd, eBPF, container events, cloud trail).
- Confirm the telemetry sources each layer needs actually exist in a typical
 deployment. This decides the architecture.

PHASE 1 — MVP / LAYER 1 (2-3 weeks)
- Collector: hash + inventory identity/persona/skill/config files per host.
- Central service: store baselines, compute drift, emit alerts.
- Alerting: webhook + email; simple dashboard. No ML.
- Deliverable: a demo where a markdown edit to an agent's persona file pages
 the operator within seconds. (This alone matches the CARBONATO TTP.)

PHASE 2 — LAYER 2 PROVENANCE (2 weeks)
- Signing/verification for agent files; manifest of approved skills/agents.
- Flag unsigned/unpinned files.

PHASE 3 — LAYER 3 RULES ENGINE (3-5 weeks)
- Rule format spec + parser; ~20 rules to start; telemetry adapters for the
 action sources (process exec, network egress, file access, scope changes).
- Rule pack publishing format (versioned, signed).

PHASE 4 — HARDENING / PACKAGING (2 weeks)
- AuthN/AuthZ, multi-tenant if needed, install path, docs, a public rule repo.

TOTAL: roughly 10-14 weeks to a capable v1, with a demoable Layer-1 result in
under a month. Phase 1 is the fastest path to a story; Phase 3 is the moat.

========================================================================
PART 5 — OPEN QUESTIONS FOR ROB (decide before dev starts)
========================================================================
1. Name — WATCHMAN / WARDEN / pick something.
2. Build vs. integrate — do we ship our own collector, or hook eBPF/auditd and
 focus entirely on the rules layer? (I lean: integrate telemetry, own rules.)
3. Target customer for v1 — internal (our own fleet) or a client pilot?
 I'd run it on OUR agents first; it makes the blog post real and the tool
 battle-tested before anyone pays.
4. Open-source the rule packs? I think yes — the rules are the community asset
 and the fastest credibility. Keep the engine's fleet/dashboard layer paid.
5. Licensed or internal-first? Related to 4.

========================================================================
PART 6 — HOW THIS TIES TO THE BLOG SERIES
========================================================================
The "Verification Is the Product" post argues the market's scarce good is
credit-proof of what an agent did, who authorized it, and what it was allowed
to touch. This tool is that thesis made concrete for the agent-security layer:
drift detection = "what changed," provenance = "who wrote it," rules engine =
"what it was allowed to do." The tool is the blog post's proof-of-concept and
the next CRPerdue service line, in one artifact.
