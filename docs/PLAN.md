# Build plan and boundaries

## Objective for the next meeting

Demonstrate free text -> controlled tags -> queryable Supabase data, using the updated
intake form and sample profiles. Admin UI, embeddings, semantic speaker search and
opportunity digests are outside this week's implementation.

## Decisions made

1. Python 3.10+ with the standard library only. This reduces setup friction in any IDE.
   Direct HTTPS requests use OpenAI Responses structured outputs; no framework needed.
2. One OpenAI call per nonempty profile, rather than a call per field. Configurable
   default `gpt-4.1-mini`; no current price promise or measured latency claim.
3. Strict JSON schema enums from the vocabulary, plus local field/quote validation.
   This rejects malformed or untraceable outputs. It does not prove label correctness.
4. Eight categories from the detailed Sample Tags list on pages 3–4. Later overlapping
   draft labels are not silently mixed in. Career stage uses role + since answer so
   explicit experience duration can support the listed numerical categories.
5. Preserve all raw form responses. Do not guess the option lists omitted in copied
   form text. Convert grids to row-label mappings; leave checkbox values as arrays.
6. Intake and tagging are separate processes, as the meeting notes requested.
7. Additive table while the existing profile schema is unknown. Production speaker
   search must eventually read from an agreed canonical profile/tag schema. A join
   example is included, but the package does not sync or backfill alumni_profiles.
8. Store tags as category arrays inside JSONB, with a GIN index. This supports the
   requested tag filtering without a schema column for every tag name.
9. Database job claims use row locks and a lease. Intake revision + token checks reject
   stale worker completion. Completed jobs are not selected repeatedly. A resubmission
   clears stale tags immediately and queues fresh work. This is at-least-once API work,
   not exactly-once billing: a crash between inference and save can cause another call.
10. Deliberate manual requeue after model failures; only HTTP 429/selected 5xx have one
    automatic OpenAI retry. Worker invocation job limits prevent an unbounded demo run.

## What is complete locally

- Python tagger, command-line setup/demo/evaluation/worker.
- Draft taxonomy and all 27 updated-form fields mapped.
- Three reconstructed sample-profile examples and five edge cases.
- Offline tests for validation, private-field exclusion, failures, headers, and worker behavior.
- Apps Script intake and grid serialization tests.
- Supabase migration and a rollback-only smoke test for duplicate/stale response handling.

## What requires the user's environment

| Input/action | Why it is needed |
| --- | --- |
| Enter personal OpenAI API key locally | Run real inference and evaluate tag quality. |
| Run supplied SQL in Supabase | Create table/RPCs and verify PostgreSQL behavior. |
| Enter Supabase URL/key locally | Worker access. No need to paste secrets into chat. |
| Updated form editor ID and edit access | Install the trigger on the actual form. |
| Team confirms vocabulary | Resolve overlapping draft lists and desired categories. |
| Existing profile schema, when integrating | Decide whether tags belong on alumni_profiles or a related table. |

If asking for integration help, share the output of `sql/000_inspect_existing.sql`.
It reads schema metadata only, not personal profile rows or API secrets.

## Acceptance checks

- Sample tagging produces allowed labels and meaningful evidence.
- Explicit no-skill/aspirational input does not become a skill.
- Names/locations without a story answer cannot produce identity story tags.
- New form captures every answer, especially grids and checkbox arrays.
- Same email updates one row; exact resend does not create a revision/job.
- Old arrival and old worker output cannot overwrite a newer submission.
- A failed API call leaves raw input intact and produces an actionable failure state.
- Queries return completed profiles matching selected categories/tags.
- Explain evaluation limitations: reconstructed samples are easy smoke tests; independent
  free-text examples and review by the team are needed before the pilot.
