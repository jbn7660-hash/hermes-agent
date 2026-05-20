# Hermes Agent — Development Guide

Instructions for AI coding assistants and developers working on the hermes-agent codebase.

> **Read me first, then go to a child doc.** This file is intentionally small. Every section
> below is a one-line summary + link to a detail doc in `docs/dev/`. Open only the docs
> relevant to your task — full-loading every doc burns the model's context budget.

---

## ⚠️ Critical context guards (NEVER violate)

1. **NEVER full-read these god-class entry points.** Each consumes
   65–89% of GPT-5.5's 256k context window alone:

   | File | LOC | Why |
   |---|---|---|
   | `gateway/run.py` | ~18,200 | `GatewayRunner` class — not yet split |
   | `cli.py` | ~14,500 | `HermesCLI` orchestrator — not yet split |
   | `hermes_cli/main.py` | ~13,200 | argparse + CLI bootstrap — not yet split |

   Workflow instead: `grep -n "def <method>"` → `Read offset=<line> limit=80`.
   Same rule for `hermes_cli/auth.py`, `tui_gateway/server.py`,
   `hermes_cli/config.py`, `hermes_cli/gateway.py`,
   `agent/auxiliary_client.py` (all > 200 KB).

   **Already split (upstream, May 2026)**: `run_agent.py` is now ~4,100 LOC
   (normal). `AIAgent.__init__` lives in `agent/agent_init.py` (~1,500 LOC),
   `run_conversation` in `agent/conversation_loop.py` (~4,100 LOC), and
   ten runtime helpers in `agent/agent_runtime_helpers.py` (~2,200 LOC).
   Chat-completion specifics in `agent/chat_completion_helpers.py`,
   compression in `agent/conversation_compression.py`,
   Codex Responses adapter in `agent/codex_runtime.py`,
   `IterationBudget` in `agent/iteration_budget.py`. Read these individually
   — none exceeds 25% of the context window.

2. **Planning / spec docs MUST be split.** Never write a single planning
   markdown over ~10 KB. Use the index-file pattern:

   ```
   <topic>/INDEX.md      # one-line summary per child + links
   <topic>/<aspect>.md   # detail per concern (≤10 KB each)
   ```

   Single-file plans bloat agent context permanently and force compact loops.
   This applies to `plans/`, `.plans/`, `docs/plans/`, and any new spec/design/RFC
   document you create.

3. **Detail docs are read on demand.** This file should remain under ~10 KB.
   When a section grows past a quick reference, push the detail into
   `docs/dev/<NN>-<topic>.md` and leave a one-line summary + link here.

---

## Entry-point map

| Concern | File | Notes |
|---|---|---|
| Agent class shell | `run_agent.py` | `AIAgent` class core (~4,100 LOC, OK to full-read) |
| Agent __init__ | `agent/agent_init.py` | extracted constructor (~1,500 LOC) |
| Agent loop | `agent/conversation_loop.py` | `run_conversation` (~4,100 LOC) |
| Agent runtime helpers | `agent/agent_runtime_helpers.py` | 10 helpers ported out of run_agent.py |
| Chat-completion specifics | `agent/chat_completion_helpers.py` | per-provider chat call helpers |
| Conversation compression | `agent/conversation_compression.py` | summarize-and-trim path |
| Codex Responses adapter | `agent/codex_runtime.py` | OpenAI Codex Responses API path |
| Iteration budget | `agent/iteration_budget.py` | shared with subagents |
| CLI orchestrator | `cli.py` | `HermesCLI` class — **slice, don't full-read** |
| Gateway runtime | `gateway/run.py` | `GatewayRunner` class — **slice, don't full-read** |
| CLI subcommands + main | `hermes_cli/main.py` | argparse, profile override — **slice** |
| Tool orchestration | `model_tools.py` | `discover_builtin_tools()`, `handle_function_call()` |
| Toolset definitions | `toolsets.py` | `_HERMES_CORE_TOOLS` list |
| Session store | `hermes_state.py` | SQLite + FTS5 search |
| Profile paths | `hermes_constants.py` | `get_hermes_home()`, `display_hermes_home()` |
| Logging setup | `hermes_logging.py` | `agent.log` / `errors.log` / `gateway.log` |
| Batch runs | `batch_runner.py` | Parallel batch processing |
| Agent internals | `agent/` | Provider adapters, memory, caching, compression |
| Tool implementations | `tools/` | Auto-discovered via `tools/registry.py` |
| Messaging adapters | `gateway/platforms/` | Per-platform (telegram, discord, slack, …) |
| Plugins | `plugins/` | Memory, model-provider, kanban, etc. |
| TUI | `ui-tui/` (Ink) + `tui_gateway/` (Python) | `hermes --tui` |
| ACP | `acp_adapter/` | VS Code / Zed / JetBrains integration |
| Cron | `cron/` | `jobs.py` + `scheduler.py` |

**User config:** `~/.hermes/config.yaml`, `~/.hermes/.env`.
**Logs:** `~/.hermes/logs/` — browse with `hermes logs [--follow] [--level …] [--session …]`.

---

## Detail docs (read on demand)

| # | Topic | File |
|---|---|---|
| 01 | Dev env + project structure + file dependency chain | [docs/dev/01-orientation.md](docs/dev/01-orientation.md) |
| 02 | AIAgent runtime + agent loop | [docs/dev/02-agent-runtime.md](docs/dev/02-agent-runtime.md) |
| 03 | CLI + TUI architecture (slash registry, Ink/PTY) | [docs/dev/03-cli-and-tui.md](docs/dev/03-cli-and-tui.md) |
| 04 | Adding new tools + toolsets | [docs/dev/04-tools.md](docs/dev/04-tools.md) |
| 05 | Adding configuration (`config.yaml`, `.env`, loaders) | [docs/dev/05-config.md](docs/dev/05-config.md) |
| 06 | Dependency pinning policy (supply-chain hygiene) | [docs/dev/06-deps.md](docs/dev/06-deps.md) |
| 07 | Skin / theme system | [docs/dev/07-skin.md](docs/dev/07-skin.md) |
| 08 | Plugins (general, memory, model-provider) | [docs/dev/08-plugins.md](docs/dev/08-plugins.md) |
| 09 | Skills (bundled + optional + HARDLINE authoring standards) | [docs/dev/09-skills.md](docs/dev/09-skills.md) |
| 10 | Delegation (`delegate_task` shapes + roles) | [docs/dev/10-delegation.md](docs/dev/10-delegation.md) |
| 11 | Curator (skill lifecycle, auto-archive) | [docs/dev/11-curator.md](docs/dev/11-curator.md) |
| 12 | Cron (scheduled jobs, hardening invariants) | [docs/dev/12-cron.md](docs/dev/12-cron.md) |
| 13 | Kanban (multi-agent work queue, dispatcher) | [docs/dev/13-kanban.md](docs/dev/13-kanban.md) |
| 14 | Important policies + profiles (cache rules, multi-instance) | [docs/dev/14-policies-and-profiles.md](docs/dev/14-policies-and-profiles.md) |
| 15 | Known pitfalls (path hardcoding, ANSI bugs, squash hazards) | [docs/dev/15-pitfalls.md](docs/dev/15-pitfalls.md) |
| 16 | Testing (`scripts/run_tests.sh`, change-detector ban) | [docs/dev/16-testing.md](docs/dev/16-testing.md) |
|    | Release notes archive | [docs/releases/](docs/releases/) |
|    | Existing planning docs | [.plans/](.plans/), [docs/plans/](docs/plans/), [plans/](plans/) |

---

## Quick start

```bash
source .venv/bin/activate   # or: source venv/bin/activate
scripts/run_tests.sh        # CI-parity test wrapper — ALWAYS use, never raw pytest
```

For everything else (adding a tool, slash command, skill, plugin, config key,
provider, platform, etc.), open the matching detail doc above.
