---
sidebar_position: 5
title: "Prompt Assembly"
description: "How Hermes builds the system prompt, preserves cache stability, and injects ephemeral layers"
---

# Prompt Assembly

Hermes deliberately separates:

- **cached system prompt state**
- **ephemeral API-call-time additions**

This is one of the most important design choices in the project because it affects:

- token usage
- prompt caching effectiveness
- session continuity
- memory correctness

Primary files:

- `agent/system_prompt.py` — entry point. `build_system_prompt_parts()` assembles all three tiers (stable / context / volatile) and returns them as a dict.
- `agent/prompt_builder.py` — helper. Context file discovery (`build_context_files_prompt`, `_find_hermes_md`, `_load_agents_md`, `_load_claude_md`), SOUL loading (`load_soul_md`), security scanning of project context files, and the static guidance strings (`MEMORY_GUIDANCE`, `SKILLS_GUIDANCE`, `TOOL_USE_ENFORCEMENT_GUIDANCE`, etc.).
- `run_agent.py` — owns the `AIAgent` runtime state; thin forwarders such as `_build_system_prompt` and `_invalidate_system_prompt` call into `agent/system_prompt.py`.
- `tools/memory_tool.py` — writes `MEMORY.md` / `USER.md` that `agent/system_prompt.py` later snapshots into the volatile tier.

## Cached system prompt layers

`build_system_prompt_parts()` in `agent/system_prompt.py` returns a `{"stable": ..., "context": ..., "volatile": ...}` dict; `build_system_prompt()` then joins all three with `\n\n` into a single string. Callers (primarily `run_agent.py`) store that joined string on `agent._cached_system_prompt` so the same prompt is reused across every turn in the session — `build_system_prompt()` itself is pure and does not touch the cache. The three tiers are an *ordering* discipline, not a per-call rebuild boundary: **stable** and **context** form the most cache-friendly prefix, and **volatile** is placed *last* so that when the cached prompt is eventually invalidated and rebuilt, the churn is confined to the suffix and the upstream prefix-cache hit on the stable + context region is preserved across rebuilds. The whole assembled string still hashes to a new value when volatile changes, so a full system-prompt cache miss is unavoidable on each rebuild — placing volatile last only minimizes *prefix* churn, not full-prompt churn.

The cached prompt is rebuilt only when something explicitly clears `agent._cached_system_prompt`. The known triggers are:

- `invalidate_system_prompt()` after context compression — `agent/conversation_compression.py`'s `compress_context()` calls it, then immediately re-assigns the freshly-built prompt
- Mid-session model swap — direct assignment in `agent/agent_runtime_helpers.py`
- CLI session lifecycle events — `/new`, `/resume`, and `/fork` paths in `cli.py` call `_invalidate_system_prompt()`
- TUI gateway `/prompt` config swap — `tui_gateway/server.py` directly nulls `_cached_system_prompt` when `agent.ephemeral_system_prompt` is reassigned

Mid-session writes to `MEMORY.md` / `USER.md` update disk state but do **not** by themselves invalidate the cached prompt — the new snapshot is only re-read on the next invalidation event (`invalidate_system_prompt()` calls `memory_store.load_from_disk()` before the rebuild).

### Stable tier (identity + guidance — cached across turns)

1. **Agent identity** — `SOUL.md` from `HERMES_HOME` via `load_soul_md()` when present, otherwise the hardcoded `DEFAULT_AGENT_IDENTITY` in `prompt_builder.py`
2. `HERMES_AGENT_HELP_GUIDANCE` (pointer to the hermes-agent skill + docs)
3. Tool-aware behavior guidance — `MEMORY_GUIDANCE`, `SESSION_SEARCH_GUIDANCE`, `SKILLS_GUIDANCE`, kanban worker/orchestrator block when relevant
4. `COMPUTER_USE_GUIDANCE` when `computer_use` tool is loaded
5. Nous subscription block (when active)
6. `TOOL_USE_ENFORCEMENT_GUIDANCE` (model-gated) — augmented by `GOOGLE_MODEL_OPERATIONAL_GUIDANCE` for Gemini/Gemma and `OPENAI_MODEL_EXECUTION_GUIDANCE` for GPT/Codex/Grok
7. Skills index (when `skills_list` / `skill_view` / `skill_manage` tools are present)
8. Alibaba model-name workaround (provider-gated)
9. Environment hints (WSL, Termux, etc.) — `build_environment_hints()`
10. Platform hint — `PLATFORM_HINTS[platform_key]` or plugin-supplied

### Context tier (cwd-dependent — cached for the session)

11. Optional caller-supplied `system_message`
12. Context files (`.hermes.md`, `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, `.cursor/rules/*.mdc`) discovered under `TERMINAL_CWD` via `build_context_files_prompt()` — `SOUL.md` is excluded here when `_soul_loaded=True` (passed in as `skip_soul=_soul_loaded`) to prevent double injection

### Volatile tier (rebuilt on prompt regeneration — placed last to minimize prefix-cache churn)

13. **MEMORY snapshot** — `agent._memory_store.format_for_system_prompt("memory")`. Mid-session writes to `MEMORY.md` update disk state but the **already-built** system prompt keeps the old snapshot until `invalidate_system_prompt()` triggers a rebuild (called by compression, mid-session model swap, or CLI session lifecycle events — see the trigger list above).
14. **USER profile snapshot** — `agent._memory_store.format_for_system_prompt("user")`. Same lifecycle as the MEMORY snapshot.
15. External memory provider block — `agent._memory_manager.build_system_prompt()` when a plugin provider is attached.
16. Timestamp + optional session ID + model + provider line. Date-only granularity (not minute-precision) to keep the prompt byte-stable for the day.

> Note: memory and USER profile sit in the **volatile** tier even though they only change once per session in practice — placing them at the *end* of the assembled prompt keeps the **stable** and **context** prefix byte-identical across turns even when the volatile suffix later rebuilds, which is what makes the upstream prefix-cache continue to hit on the leading region. The whole assembled prompt — stable + context + volatile — is still cached as one string on `agent._cached_system_prompt`; on a rebuild the cache key for the *full* prompt changes, but the leading stable + context bytes are unchanged so prefix-aware caches still benefit. Regeneration runs only when `_cached_system_prompt` is explicitly invalidated (compression, model swap, or one of the CLI / TUI lifecycle events listed above).

### SOUL.md load condition

`agent/system_prompt.py:89-94` gates SOUL loading on `if agent.load_soul_identity or not agent.skip_context_files:`. So:

| `load_soul_identity` | `skip_context_files` | Result |
|---------------------:|---------------------:|--------|
| `False` (default)    | `False` (default)    | SOUL loaded as identity, project context files loaded |
| `False`              | `True` (e.g. subagent delegation) | SOUL **not** loaded — fall back to `DEFAULT_AGENT_IDENTITY`, project context files skipped |
| `True` (e.g. cron worker) | `True` | SOUL **still loaded** as identity; project context files skipped |
| `True`               | `False`              | SOUL loaded; project context files loaded (same as default but explicit) |

Setting `load_soul_identity=True` is how cron mode and similar isolated execution paths keep the `HERMES_HOME` persona while disabling cwd-derived project instructions. The `_soul_loaded` flag is then passed as `skip_soul=_soul_loaded` into `build_context_files_prompt()` so SOUL.md is not injected a second time as a project context file.

### Concrete example: assembled system prompt

Here is a simplified view of what the final system prompt looks like when all layers are present (comments show the tier and the source of each section). The three tiers are joined with `\n\n` in this order: `stable`, `context`, `volatile`.

```
# ── Stable tier (cached across all turns) ─────────────────────────

# [stable] Agent Identity (from ~/.hermes/SOUL.md)
You are Hermes, an AI assistant created by Nous Research.
You are an expert software engineer and researcher.
You value correctness, clarity, and efficiency.
...

# [stable] HERMES_AGENT_HELP_GUIDANCE
For questions about Hermes itself, load the hermes-agent skill.

# [stable] Tool-aware behavior guidance
You have persistent memory across sessions. Save durable facts using
the memory tool: user preferences, environment details, tool quirks,
and stable conventions. Memory is injected into every turn, so keep
it compact and focused on facts that will still matter later.
...
When the user references something from a past conversation or you
suspect relevant cross-session context exists, use session_search
to recall it before asking them to repeat themselves.

# [stable] TOOL_USE_ENFORCEMENT_GUIDANCE (for GPT/Codex/Grok models only)
You MUST use your tools to take action — do not describe what you
would do or plan to do without actually doing it.
...

# [stable] Skills index
## Skills (mandatory)
Before replying, scan the skills below. If one clearly matches
your task, load it with skill_view(name) and follow its instructions.
...
<available_skills>
  software-development:
    - code-review: Structured code review workflow
    - test-driven-development: TDD methodology
  research:
    - arxiv: Search and summarize arXiv papers
</available_skills>

# [stable] Environment hints + platform hint
You are a CLI AI Agent. Try not to use markdown but simple text
renderable inside a terminal.

# ── Context tier (cwd-dependent, cached for the session) ──────────

# [context] Optional caller-supplied system message
[User-configured system message override]

# [context] Project context files (from project directory)
# Project Context
The following project context files have been loaded and should be followed:

## AGENTS.md
This is the atlas project. Use pytest for testing. The main
entry point is src/atlas/main.py. Always run `make lint` before
committing.

# ── Volatile tier (rebuilt on prompt regeneration — placed last to minimize prefix-cache churn) ─────

# [volatile] MEMORY snapshot
## Persistent Memory
- User prefers Python 3.12, uses pyproject.toml
- Default editor is nvim
- Working on project "atlas" in ~/code/atlas
- Timezone: US/Pacific

# [volatile] USER profile snapshot
## User Profile
- Name: Alice
- GitHub: alice-dev

# [volatile] Timestamp + session metadata
Conversation started: Monday, March 30, 2026
Session ID: abc123
Model: claude-sonnet-4.6
Provider: anthropic
```

## How SOUL.md appears in the prompt

`SOUL.md` lives at `~/.hermes/SOUL.md` and serves as the agent's identity — the very first section of the system prompt. The loading logic in `prompt_builder.py` works as follows:

```python
# From agent/prompt_builder.py (simplified)
def load_soul_md() -> Optional[str]:
    soul_path = get_hermes_home() / "SOUL.md"
    if not soul_path.exists():
        return None
    content = soul_path.read_text(encoding="utf-8").strip()
    content = _scan_context_content(content, "SOUL.md")  # Security scan
    content = _truncate_content(content, "SOUL.md")       # Cap at 20k chars
    return content
```

When `load_soul_md()` returns content, it replaces the hardcoded `DEFAULT_AGENT_IDENTITY`. The `build_context_files_prompt()` function is then called with `skip_soul=True` to prevent SOUL.md from appearing twice (once as identity, once as a context file).

If `SOUL.md` doesn't exist, the system falls back to:

```
You are Hermes Agent, an intelligent AI assistant created by Nous Research.
You are helpful, knowledgeable, and direct. You assist users with a wide
range of tasks including answering questions, writing and editing code,
analyzing information, creative work, and executing actions via your tools.
You communicate clearly, admit uncertainty when appropriate, and prioritize
being genuinely useful over being verbose unless otherwise directed below.
Be targeted and efficient in your exploration and investigations.
```

## How context files are injected

`build_context_files_prompt()` uses a **priority system** — only one project context type is loaded (first match wins):

```python
# From agent/prompt_builder.py (simplified)
def build_context_files_prompt(cwd=None, skip_soul=False):
    cwd_path = Path(cwd).resolve()

    # Priority: first match wins — only ONE project context loaded
    project_context = (
        _load_hermes_md(cwd_path)       # 1. .hermes.md / HERMES.md (walks to git root)
        or _load_agents_md(cwd_path)    # 2. AGENTS.md (cwd only)
        or _load_claude_md(cwd_path)    # 3. CLAUDE.md (cwd only)
        or _load_cursorrules(cwd_path)  # 4. .cursorrules / .cursor/rules/*.mdc
    )

    sections = []
    if project_context:
        sections.append(project_context)

    # SOUL.md from HERMES_HOME (independent of project context)
    if not skip_soul:
        soul_content = load_soul_md()
        if soul_content:
            sections.append(soul_content)

    if not sections:
        return ""

    return (
        "# Project Context\n\n"
        "The following project context files have been loaded "
        "and should be followed:\n\n"
        + "\n".join(sections)
    )
```

### Context file discovery details

| Priority | Files | Search scope | Notes |
|----------|-------|-------------|-------|
| 1 | `.hermes.md`, `HERMES.md` | CWD up to git root | Hermes-native project config |
| 2 | `AGENTS.md` | CWD only | Common agent instruction file |
| 3 | `CLAUDE.md` | CWD only | Claude Code compatibility |
| 4 | `.cursorrules`, `.cursor/rules/*.mdc` | CWD only | Cursor compatibility |

All context files are:
- **Security scanned** — checked for prompt injection patterns (invisible unicode, "ignore previous instructions", credential exfiltration attempts)
- **Truncated** — capped at 20,000 characters using 70/20 head/tail ratio with a truncation marker
- **YAML frontmatter stripped** — `.hermes.md` frontmatter is removed (reserved for future config overrides)

## API-call-time-only layers

These are intentionally *not* persisted as part of the cached system prompt:

- `ephemeral_system_prompt`
- prefill messages
- gateway-derived session context overlays
- later-turn Honcho recall injected into the current-turn user message

This separation keeps the stable prefix stable for caching.

## Memory snapshots

Local memory and user profile data are injected as frozen snapshots at session start. Mid-session writes update disk state but do not mutate the already-built system prompt until a new session or forced rebuild occurs.

## Context files

`agent/prompt_builder.py` scans and sanitizes project context files using a **priority system** — only one type is loaded (first match wins):

1. `.hermes.md` / `HERMES.md` (walks to git root)
2. `AGENTS.md` (CWD at startup; subdirectories discovered progressively during the session via `agent/subdirectory_hints.py`)
3. `CLAUDE.md` (CWD only)
4. `.cursorrules` / `.cursor/rules/*.mdc` (CWD only)

`SOUL.md` is loaded separately via `load_soul_md()` for the identity slot. When it loads successfully, `build_context_files_prompt(skip_soul=True)` prevents it from appearing twice.

Long files are truncated before injection.

## Skills index

The skills system contributes a compact skills index to the prompt when skills tooling is available.

## Supported prompt customization surfaces

Most users should treat `agent/prompt_builder.py` as implementation code, not a configuration surface. The supported customization path is to change the prompt inputs Hermes already loads, rather than editing Python templates in place.

### Use these surfaces first

- `~/.hermes/SOUL.md` — replace the built-in default identity block with your own agent persona and standing behavior.
- `~/.hermes/MEMORY.md` and `~/.hermes/USER.md` — provide durable cross-session facts and user profile data that should be snapshotted into new sessions.
- Project context files such as `.hermes.md`, `HERMES.md`, `AGENTS.md`, `CLAUDE.md`, or `.cursorrules` — inject repo-specific working rules.
- Skills — package reusable workflows and references without editing core prompt code.
- Optional system prompt config / API overrides — add deployment-specific instruction text without forking Hermes.
- Ephemeral overlays such as `HERMES_EPHEMERAL_SYSTEM_PROMPT` or prefill messages — add turn-scoped guidance that should not become part of the cached prompt prefix.

### When to edit code instead

Edit `agent/prompt_builder.py` only if you are intentionally maintaining a fork or contributing upstream behavior changes. That file assembles the prompt plumbing, cache boundaries, and injection order for every session. Direct edits there are global product changes, not per-user prompt customization.

In other words:

- if you want a different assistant identity, edit `SOUL.md`
- if you want different repo rules, edit project context files
- if you want reusable operating procedures, add or modify skills
- if you want to change how Hermes assembles prompts for everyone, change Python and treat it as a code contribution

## Why prompt assembly is split this way

The architecture is intentionally optimized to:

- preserve provider-side prompt caching
- avoid mutating history unnecessarily
- keep memory semantics understandable
- let gateway/ACP/CLI add context without poisoning persistent prompt state

## Related docs

- [Context Compression & Prompt Caching](./context-compression-and-caching.md)
- [Session Storage](./session-storage.md)
- [Gateway Internals](./gateway-internals.md)
