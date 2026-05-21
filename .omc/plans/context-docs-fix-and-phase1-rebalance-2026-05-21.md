# Context Docs Fix + Phase 1 Sprint Rebalance

**Date**: 2026-05-21
**Author**: minsu (jbn7660)
**Base**: `jbn-fork/main` HEAD `6ce1a154e` (PR #8 bundled skills+session helpers merge)
**Plan type**: Harness 3-stage (Track A auto-execute / Track C queue)
**Amendment**: v2 — Codex 5.5 plan-gate round 2 HOLD 5 P1 반영 (jbn-fork main 재fetch + Track B 완료 처리 + AC docusaurus build 명시 + cross-doc audit)

---

## 1. Goal

사용자 제기 7개 claim 검증 결과를 context 문서 3개 (`prompt-assembly.md`, `context-compression-and-caching.md`, `agent-loop.md`) 에 반영하여 코드-문서 정합성을 100% 회복하고, Phase 1 gateway helper 추출 sprint 종료를 기록한다. Track A (doc fix) 는 이번 turn 자동 진행, Track C (deferred decision) 는 사용자 결정 queue.

---

## 2. Non-goals

- **코드 변경 없음** — doc-only PR. `*.py` 파일 수정 0.
- **Phase 1 helper 추출 추가 진행 없음** — text/time/replay/media/skills/session 6 module 모두 MERGED (PR #3~#8). Phase 1 종료.
- **Phase 4 god-class mixin 분해 안 함** — `run_agent.py` 18k LOC 추가 분해는 별 sprint.
- **사용자 stash replay 안 함** — `wip/stash-2026-05-20-uncommitted` (`c4b8ecbf7`) 는 수동 결정 대기.
- **CI flake fix 안 함** — `test_no_session_attached_returns_error` 순서 의존 flake 는 Track C.
- **init_snapshot hermetic-isolation PR 안 함** — 환경 의존 drift 는 Track C.
- **upstream sync 안 함** — 현재 `6ce1a154e` 기준으로 branch. origin/main upstream drift 는 PR 시점 rebase 결정.
- **architecture 재설계 안 함** — 기존 문서 구조 유지, 틀린 부분만 정정.

---

## 3. Scope

### Track A — Context Doc 3-File Fix (이번 turn, auto-execute)

#### 3-A-1. `website/docs/developer-guide/prompt-assembly.md`

| Claim | Verdict | 수정 내용 | 영향 줄 |
|-------|---------|-----------|---------|
| **#1 TRUE** | Primary files 에 `agent/system_prompt.py` 누락 | `:21-25` "Primary files" 목록에 `agent/system_prompt.py` 추가, 각 파일 역할 1줄 annotation 추가. `prompt_builder.py` 는 "helper (context file discovery + SOUL loading)" 로 정정. `system_prompt.py` 를 "entry point — `build_system_prompt_parts()` assembles all layers" 로 명시 | 21-25 |
| **#2 PARTIAL** | `skip_context_files=True` 시 SOUL 항상 skip 은 잘못 | `:42` 문장 재작성. 실제 동작: `load_soul_identity=True` 면 `skip_context_files` 와 무관하게 SOUL 유지. `system_prompt.py:89-94` 조건 `if agent.load_soul_identity or not agent.skip_context_files:` 반영. double-injection 방지 (`skip_soul=_soul_loaded`) 도 명시 | 42 |
| **#3 TRUE** | memory/profile 이 stable layer 로 표기되나 실제 volatile | `:29-41` layer 목록 재구조. `system_prompt.py:241-259` 기준으로 volatile_parts 에 memory/profile 이 추가됨을 명시. cache 무효화 영향 주의사항 추가. 3-tier (stable/context/volatile) 아키텍처 explicit 명시 | 29-41 |
| **#4 TRUE (이미 정확)** | 수정 불필요 | 변경 없음 (`:188-199` discovery 규칙 정확 확인) | — |

#### 3-A-2. `website/docs/developer-guide/context-compression-and-caching.md`

| Claim | Verdict | 수정 내용 | 영향 줄 |
|-------|---------|-----------|---------|
| **#5 PARTIAL** | summary template 7 섹션 → 실제 13 섹션 | `:159-185` template block 전체 교체. `context_compressor.py` 기준 13 필드 전체 명시: (1) Active Task (2) Goal (3) Constraints & Preferences (4) Completed Actions (5) Active State (6) In Progress (7) Blocked (8) Key Decisions (9) Resolved Questions (10) Pending User Asks (11) Relevant Files (12) **Remaining Work** (13) Critical Context. "Active Task" 이 SINGLE MOST IMPORTANT FIELD 임을 강조. "Next Steps" → "Remaining Work" rename 반영 | 159-185 |
| **#5 보충 (cross-doc)** | Before/After example 의 "Next Steps" label | `:261` example 내 "Next Steps" 도 "Remaining Work" 로 일괄 교체. 다른 doc 4개 (`quickstart.md`, `messaging/index.md`, `microsoft-graph-app-registration.md`, `creative-humanizer.md`) 의 `## Next Steps` 섹션은 summary template 무관 일반 nav 섹션 = 영향 없음 (audit 완료) | 261 |
| **#5 보충** | tail cut 메커니즘 미기술 | template block 뒤에 "Tail Boundary" 설명 추가. `_find_tail_cut_by_tokens` (`context_compressor.py`): token budget primary + min floor=3 messages + last-user-message anchor | 185+ (신규) |
| **#5 보충** | summary failure 분기 미기술 | Phase 3 설명 근처에 `abort_on_summary_failure` param (`context_compressor.py`) 분기 추가: `True` = abort + messages 보존, `False` (default) = static fallback + middle drop | 150-153 근처 |

#### 3-A-3. `website/docs/developer-guide/agent-loop.md`

| Claim | Verdict | 수정 내용 | 영향 줄 |
|-------|---------|-----------|---------|
| **#6 TRUE (line 정정)** | session lineage rotation 코드 위치 | `:213` "session lineage ID" 언급 근처에 정확한 source reference 추가: `conversation_compression.py` 의 `compress_context()` 안 (`session_id = f"{datetime.now()...}_{uuid.uuid4().hex[:6]}"`, `parent_session_id=old_session_id`). `run_agent.py` `_compress_context()` 는 thin forwarder | 213 근처 |
| **#7 TRUE (이미 정확)** | gateway 85% / agent 50% 이원 구조 | 문서 이미 정확. 변경 없음. 명확성 향상 위해 `:203-204` 에 source reference 보강만: `gateway/run.py` `_hyg_threshold_pct = 0.85` + `context_compressor.py` default `threshold_percent=0.50`. orchestration entry = `conversation_compression.py` `compress_context()` + `conversation_loop.py` docstring | 203-204 |
| **#7 보충** | `agent-loop.md:15` `prompt_builder.py` 단독 표기 | `:15` "via `prompt_builder.py`" → "via `agent/system_prompt.py` (entry point) and `agent/prompt_builder.py` (context file helpers)" 로 정정 (Claim #1 과 일관성) | 15 |
| **#7 보충** | Key Source Files 테이블 | `:226` `agent/prompt_builder.py` description 을 "Context file discovery, SOUL loading, security scanning" 으로 정정. `agent/system_prompt.py` 행 신규 추가: "System prompt assembly entry point — `build_system_prompt_parts()`" | 226 |

### Track B — Phase 1 Gateway Helper 추출: ✅ COMPLETE (5/21 종료)

| 순서 | Module | Status | PR |
|------|--------|--------|-----|
| 1 | `gateway/_helpers/text.py` | MERGED | PR #3 (`1e3f00e38`) |
| 2 | `gateway/_helpers/time.py` | MERGED | PR #4 (`b488c1014`) |
| 3 | `gateway/_helpers/replay.py` | MERGED | PR #7 (`9986085a6`) |
| 4 | `gateway/_helpers/media.py` | MERGED | PR #6 (`0c20f5274`) |
| 5 | `gateway/_helpers/skills.py` | MERGED | PR #8 (`6ce1a154e` bundled) |
| 6 | `gateway/_helpers/session.py` | MERGED | PR #8 (`6ce1a154e` bundled) |

**Phase 1 종료 — `gateway/run.py` LOC delta**: 18,205 (start) → ?(post-PR #8). 다음 Phase 4 (god-class mixin) 는 별 sprint queue.

### Track C — 사용자 결정 갈림길 (deferred, manual decision required)

| Item | Status | 결정 필요 |
|------|--------|-----------|
| 사용자 stash replay (`c4b8ecbf7`) | deferred | `run_agent.py` hunk → `agent/agent_init.py` reposition 의무. 자동 replay 금지. context rollover feature + launchd/hydration gateway fixes + external_llm_tools 12-file |
| CI flake `test_no_session_attached_returns_error` | ack | browser_eval_supervisor 순서 의존. 별 PR (hermetic test isolation) |
| init_snapshot 환경 의존 drift | ack | prompt_cache fixture baseline SHA 갱신 완료 (PR #5). 잔여 = hermetic-isolation 별 PR |
| backup branch 보존 | 절대 삭제 금지 | `backup/minsu-launchd-status-fallback-2026-05-20-pre-sync` (사용자 stash recovery source) |
| `minsu/launchd-status-fallback` branch | active | 3 commit ahead of `2b247faff` (launchd-fallback + wildcard channel + recovery snapshot). doc-fix branch 와 분리 운영 |

---

## 4. Acceptance Criteria

- [ ] **AC-1**: 3 doc file 만 수정. `*.py` diff = 0
- [ ] **AC-2**: `cd website && npm run build` 통과 (docusaurus build)
- [ ] **AC-3**: 7 claim verdict 전부 반영 — Claim 4, 7 은 무수정 확인, 나머지 5개 수정 완료
- [ ] **AC-4**: `prompt-assembly.md` Primary files 목록에 `agent/system_prompt.py` 명시 + 역할 annotation
- [ ] **AC-5**: `prompt-assembly.md:42` SOUL skip 조건 정정 — `load_soul_identity` OR `not skip_context_files` 분기 반영
- [ ] **AC-6**: `prompt-assembly.md` layer 목록에서 memory/profile 의 volatile 특성 명시 + 3-tier 아키텍처 explicit
- [ ] **AC-7**: `context-compression-and-caching.md` summary template = 13 필드 전체 명시, "Active Task" SINGLE MOST IMPORTANT 강조, "Next Steps" → "Remaining Work" 전부 교체
- [ ] **AC-8**: tail boundary (`_find_tail_cut_by_tokens`) + summary failure (`abort_on_summary_failure`) 설명 추가
- [ ] **AC-9**: `agent-loop.md` session lineage = `conversation_compression.py` `compress_context()` 정확 참조
- [ ] **AC-10**: `agent-loop.md` Key Source Files 에 `system_prompt.py` 행 추가 + `prompt_builder.py` 설명 정정
- [ ] **AC-11**: Before/After example 내 "Next Steps" → "Remaining Work" rename 반영 (`:261`)
- [ ] **AC-12**: 모든 source reference 가 함수명/변수명 anchor 사용 (line number only 금지 — drift 방지)
- [ ] **AC-13**: cross-doc audit 명시 — 다른 4 doc 의 `## Next Steps` 섹션은 summary template 무관 nav (이미 검증)

---

## 5. Risks + Mitigations

| # | Risk | Severity | Mitigation |
|---|------|----------|------------|
| R1 | doc 내 line number reference 가 코드 변경으로 drift | MEDIUM | 함수명 anchor (`build_system_prompt_parts()`, `_find_tail_cut_by_tokens`, `compress_context()`) + 짧은 코드 인용 사용. deep link (`file:line`) 는 보조 정보로만 |
| R2 | jbn-fork main 과 origin upstream drift → doc 이 upstream 코드와 불일치 | LOW | branch base 명시 `jbn-fork/main` `6ce1a154e`. doc 은 이 시점 코드 기준. upstream sync 는 별 절차 |
| R3 | Phase 1 sprint 와 doc fix 가 같은 file 충돌 | NONE | Phase 1 종료 (5/21). file overlap 0 |
| R4 | summary template 13 필드 명시 후 upstream 이 template 변경 | LOW | code inline comment 에 "see also: website/docs/developer-guide/context-compression-and-caching.md" 추가 제안 (별 PR). 현재는 snapshot 정확성 우선 |
| R5 | docusaurus build 실패 (broken link / syntax) | MEDIUM | Stage 3 Layer 1 에서 `cd website && npm run build` 의무. 실패 시 fix 후 재시도 |
| R6 | "Next Steps" → "Remaining Work" rename 시 cross-doc 깨짐 | NONE | audit 결과: 다른 4 doc 의 `## Next Steps` 는 summary template 무관 nav 섹션. impact 없음 |

---

## 6. Orchestration

### Stage 1: Planner (이 문서)

- [x] Plan artifact 작성 v1 → `.omc/plans/context-docs-fix-and-phase1-rebalance-2026-05-21.md`
- [x] Codex 5.5 plan-gate round 1 (HOLD) → 5 P1 반영 → Plan v2 amend
- [ ] Codex 5.5 plan-gate round 2 (v2 검증) — optional, 변경 작아 SHIP 판정 시 skip 가능

### Stage 2: Generator (executor agent, opus)

- Branch: `docs/context-architecture-fix-2026-05-21` from `jbn-fork/main` (`6ce1a154e`)
- **Single commit** (doc-only 이므로 1 commit 허용)
- 3 file 일괄 수정:
  1. `website/docs/developer-guide/prompt-assembly.md` — Claim #1, #2, #3 반영
  2. `website/docs/developer-guide/context-compression-and-caching.md` — Claim #5 반영 (13-field + tail + failure)
  3. `website/docs/developer-guide/agent-loop.md` — Claim #6, #7 보강 + system_prompt.py 일관성

### Stage 3: Evaluator (3-layer gate)

#### Layer 1 — 자동 검증
- [ ] `cd website && npm run build` 통과 (docusaurus build, broken link 0)
- [ ] `grep -n "## Next Steps" website/docs/developer-guide/context-compression-and-caching.md` = 0 hits (summary template 영역 전부 "Remaining Work" 로 교체)
- [ ] `grep -n "system_prompt.py" website/docs/developer-guide/prompt-assembly.md` >= 2 hits (Primary files + 본문)
- [ ] `grep -c "Active Task" website/docs/developer-guide/context-compression-and-caching.md` >= 1
- [ ] `grep -n "Remaining Work" website/docs/developer-guide/context-compression-and-caching.md` >= 2 (template + example)
- [ ] `grep -n "load_soul_identity" website/docs/developer-guide/prompt-assembly.md` >= 1
- [ ] `grep -n "volatile" website/docs/developer-guide/prompt-assembly.md` >= 1 (3-tier 명시)
- [ ] `grep -n "_find_tail_cut_by_tokens\|abort_on_summary_failure" website/docs/developer-guide/context-compression-and-caching.md` >= 2
- [ ] `grep -n "conversation_compression" website/docs/developer-guide/agent-loop.md` >= 1 (lineage rotation 참조)
- [ ] typecheck / lint / test 영향 없음 (doc-only 변경)

#### Layer 2 — 내부 리뷰
- [ ] `code-reviewer` agent: 3 doc diff 검토 (정확성, 일관성, 누락)
- [ ] `verifier` agent: AC-1 ~ AC-13 체크리스트 대조

#### Layer 3 — 외부 adversarial
- [ ] Codex 5.5 diff-gate: PR diff 전달, severity-rated finding. P0 = 차단, P1 = ack. 결과 `/tmp/codex-review-context-docs-2026-05-21.md` 보존

---

## 7. Deliverables

### Track A (이번 turn)

| # | Deliverable | 상세 |
|---|-------------|------|
| D1 | Branch | `docs/context-architecture-fix-2026-05-21` from `jbn-fork/main` (`6ce1a154e`) |
| D2 | Commit | single commit, message: `docs(context): fix prompt-assembly + compression + agent-loop architecture description` |
| D3 | PR | title: `docs(context): fix prompt-assembly + compression + agent-loop architecture description` (jbn-fork 기반 ad-hoc, upstream PR 별도 결정) |
| D4 | PR body | 의무 섹션: **Background** (사용자 7 claim 요약) + **Verdict Table** (7 row, TRUE/PARTIAL/이미정확) + **Changes** (file-by-file bullet) + **Verification** (grep + docusaurus build) + **Codex review link** (`/tmp/codex-review-context-docs-2026-05-21.md`) |
| D5 | Plan-gate result | `/tmp/codex-plan-context-docs-fix-2026-05-21.md` (round 1 HOLD + round 2 SHIP 가능) |
| D6 | Diff-gate result | `/tmp/codex-review-context-docs-2026-05-21.md` |

### Track B — ✅ Phase 1 COMPLETE (이번 sprint에 추가 작업 없음)

### Track C — 사용자 결정 queue (메모리 entry 로 명시)

| # | Item | Trigger |
|---|------|---------|
| C1 | Stash replay 결정 | 사용자 명시 결정 시 |
| C2 | CI flake hermetic-isolation PR | flake 재발 시 |
| C3 | init_snapshot hermetic-isolation PR | drift 발생 시 |

### Memory entries (post-merge)

1. `project_2026_05_21_context_docs_fix_pr_shipped.md` — PR merge 후 결과, verdict table, Codex review link
2. `feedback_doc_line_reference_anchor_pattern.md` — line number anchor → 함수명 anchor lesson (R1 mitigation 의 일반화)
3. 기존 `project_2026_05_21_hermes_phase1_pr3_pr4_merged_pr5_open.md` 갱신 → "Phase 1 COMPLETE (PR #6/7/8 추가 merge 확인)" + Track C 큐 명시

---

## Appendix A: Claim Verdict Reference (OMC 2-way 검증 완료)

| # | Claim | Verdict | File | Action |
|---|-------|---------|------|--------|
| 1 | `prompt-assembly.md` Primary files 에 `system_prompt.py` 누락 | **TRUE** | prompt-assembly.md:21-25 | 추가 + annotation |
| 2 | `skip_context_files=True` 시 SOUL 항상 skip | **PARTIAL** | prompt-assembly.md:42 | 조건 정정 |
| 3 | memory/profile 이 stable layer 5-6 위치 | **TRUE** | prompt-assembly.md:29-41 | volatile 특성 명시 + 3-tier explicit |
| 4 | context file discovery 설명 | **TRUE (이미 정확)** | prompt-assembly.md:188-199 | 수정 불필요 |
| 5 | summary template 7 섹션 (실제 13) | **PARTIAL** | context-compression.md:159-185 | 13 필드 전체 교체 + Active Task 강조 + Next Steps→Remaining Work |
| 6 | session lineage rotation 위치 | **TRUE (line 정정)** | agent-loop.md:213 | `conversation_compression.py` `compress_context()` 정확 참조 |
| 7 | gateway 85% / agent 50% | **TRUE (이미 정확)** | agent-loop.md:203-204 | source ref 보강만 + Key Source Files 갱신 |

## Appendix B: Phase 1 Sprint Timeline (✅ COMPLETE)

```
2026-05-20  PR #1 docs split          MERGED (584564062)
2026-05-20  PR #2 audit scripts        MERGED (9d238642f)
2026-05-21  PR #3 text.py              MERGED (1e3f00e38)
2026-05-21  PR #4 time.py              MERGED (b488c1014)
2026-05-21  PR #5 prompt_cache fixture  MERGED (2b247faff)
2026-05-21  PR #6 media.py             MERGED (0c20f5274)
2026-05-21  PR #7 replay.py            MERGED (9986085a6)
2026-05-21  PR #8 skills+session       MERGED (6ce1a154e) ← Phase 1 종료
2026-05-21  PR __ docs fix             ← THIS PLAN (Track A)
```

## Appendix C: Codex 5.5 Plan-gate Findings (round 1 HOLD → v2 반영)

| Finding | Severity | Status |
|---------|----------|--------|
| jbn-fork/main HEAD stale (`2b247faff` → `6ce1a154e`) | P1 | ✅ Plan v2 base 갱신 |
| Track B/C sections invalid (helper 모두 merged) | P1 | ✅ Track B = COMPLETE 표기 |
| AC-2 "typecheck/lint/test impact" too fuzzy | P1 | ✅ `cd website && npm run build` 명시 |
| Missing cross-doc audit "Next Steps" rename | P1 | ✅ R6 + AC-13 추가 (4 doc nav 섹션 무관) |
| Overall stop condition (re-plan required) | P1 | ✅ v2 amend 완료 |
