# Content Intelligence Module Specification

**Date:** 2026-09-22
**Issue:** #12 Content intelligence: Bocha, scripts, and automatic review
**Target branch:** `feat/content-intelligence`

## Goal

Build a testable, replaceable content-intelligence module that searches enabled topics, produces daily recommendations, generates three script candidates for a selected topic, and emits structured automatic-review results.

The module submits content results and events only. It does not persist approvals, mutate task state, generate media, or publish content.

## Scope

### Included

- Search through a Bocha adapter.
- Keep at most 10 recommendations per enabled topic per day, ordered by relevance.
- Generate script candidates for at most two rounds.
- Validate script length, conversational style, factual sources, and candidate differences.
- Emit `LOW`, `HIGH`, and `BLOCKED` risk levels.
- Record provider, model, prompt version, calls, cost, latency, sources, and review evidence.
- Provide deterministic test adapters that do not require real Bocha or LLM credentials.

### Excluded

- Approval persistence.
- Task state transitions, including `PAUSED`.
- Media production, media QC, and publishing.
- Browser-side calls to Bocha, LLM, or other providers.
- Committing API keys or other secrets.

## Module interfaces

Provider calls must be isolated behind ports. Domain services must not depend directly on provider SDKs.

```python
class SearchProvider(Protocol):
    def search(self, query: str, limit: int) -> SearchBatch: ...


class ScriptProvider(Protocol):
    def generate(self, prompt: str, model: str) -> GeneratedScript: ...
```

Test adapters must return deterministic results for fixed inputs and support provider failures, insufficient sources, low-quality candidates, and fewer-than-three-candidates scenarios.

The approved first-batch plan refines the search return value to `SearchBatch`, containing immutable `results` and a batch-level `call` record. Empty search results therefore retain their audit record. Unknown source times, model metadata, token counts, and costs remain `None`; implementations must not invent these values.

## Recommendations

For each enabled topic, run one daily search:

- Keep at most 10 recommendations.
- Sort by relevance score descending.
- Include topic ID, title, summary, URL, relevance score, and source time for every item.
- Allow unselected recommendations to be replaced on a later date.
- Make every result traceable to a provider call record.
- Submit results through `topic.discovered.v1`.

The recommendation service must not create tasks or mutate task state.

## Script candidates

For one selected topic:

- Produce exactly 3 qualified candidates when generation succeeds.
- Use no more than 2 generation rounds.
- Keep each script body between 35 and 55 Chinese characters.
- Use short, conversational sentences.
- Prioritize facts and forbid exaggerated promises.
- Cite at least 2 distinct URLs per candidate.
- Map each key fact to source evidence.
- Make the opening, structure, and main wording observably different across candidates.
- If fewer than 3 candidates remain after two rounds, submit an insufficient-candidates result and let workflow decide whether to move the task to `PAUSED`.
- Never mutate task state inside content intelligence.

Script results use `task.script_reviewed.v1` or a dedicated result event agreed by the team. If the existing contract cannot express candidate details, update the versioned contract before implementing the producer.

## Automatic review

Review rules must be deterministic and unit-testable.

### `BLOCKED`

- A confirmed blocking rule is matched.
- A key fact cannot be verified.
- The source count is insufficient.
- The content contains forbidden absolute, illegal, unsafe, or dangerous promises.

`BLOCKED` results must not enter human approval.

### `HIGH`

- A high-risk topic or rule is matched.
- Evidence requires human review but is not an explicit block.
- The result requires two consistent approvals from different accounts.

### `LOW`

- No blocking or high-risk rule is matched.
- Sources and key-fact evidence are complete.
- The result requires one approval.

The review result must include at least:

```json
{
  "risk_level": "LOW|HIGH|BLOCKED",
  "rule_hits": [],
  "evidence": [],
  "requires_human_review": true,
  "approval_requirement": "NONE|SINGLE|TWO_DISTINCT_APPROVERS"
}
```

Approval requirements are output metadata only. Approval services own persistence and gates.

## Audit records

Every Bocha or LLM call records at least:

- Call ID.
- Provider.
- Model.
- Prompt version.
- Input and output token counts.
- Cost.
- Start time, finish time, and latency.
- Source URLs used.
- Review rules and evidence.

Logs, events, and tests must never contain API keys or other secrets.

## Event boundary

Consumed and produced events follow the shared envelope in `contracts/`:

```text
event_id
event_type
occurred_at
correlation_id
idempotency_key
data
```

If candidate details, evidence mapping, or approval requirements cannot be expressed by an existing event, confirm fields with the business-API and workflow owners before changing the versioned contract. Content intelligence must not bypass events to write the business database.

## Acceptance criteria

- [ ] Each topic produces no more than 10 auditable, relevance-sorted recommendations.
- [ ] Three script candidates satisfy the 35–55 Chinese-character, two-URL, and differentiation rules.
- [ ] Insufficient candidates after two rounds do not produce fake qualified results or mutate task state.
- [ ] `BLOCKED` cannot produce an approvable result.
- [ ] `HIGH` outputs two-person approval requirements and `LOW` outputs one-person approval requirements.
- [ ] Test adapters make identical inputs produce identical outputs.
- [ ] Provider, model, prompt, cost, latency, sources, and review evidence are traceable.
- [ ] Existing shared contract tests and new content-intelligence tests pass.
