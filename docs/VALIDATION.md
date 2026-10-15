# Validation record

Prepared October 8, 2026.

| Check | Result | What it establishes |
| --- | --- | --- |
| `python3 -m unittest discover -s tests -v` | 30 passed | Input filtering, controlled labels, evidence quotes, response refusal/incompleteness handling, HTTP retry limits, key headers and worker control flow. API/database clients mocked. |
| `node tests/test_form_adapter.cjs` | Passed | Question mapping matches configuration; checkbox arrays, grid row labels, unknown-question preservation, missing and duplicate identity-field errors. Google services mocked. |
| `node tests/test_database.cjs` with PGlite 0.5.8 | Passed | Executes the actual migration and rollback smoke SQL in isolated PostgreSQL. Adds 12 claim, expiry/reclaim, result and permission checks with simulated Supabase roles. |
| `python3 -m compileall -q alumni_tagger tests` | Passed | Python syntax/import compilation. |
| `python3 -m alumni_tagger --help` | Passed | CLI command entry point. |
| Taxonomy/mapping checks | Passed | 8 categories, no duplicate labels within a category; 27 form questions mapped; all sample expected labels exist in taxonomy. |

Not yet run: a real OpenAI request, paid live evaluation, SQL against the user's
Supabase project, PostgREST RPC calls with real Supabase keys, or a real Google
form submission. No live classification accuracy or production-readiness claim
is made. PGlite role tests do not replace a live Supabase security/integration test.

The optional PGlite developer test needs Node and its test package. Running the
Python program itself requires only Python 3.10+ and no pip/npm dependencies.

## October 13 update

| Check | Result | What it establishes |
| --- | --- | --- |
| `python3 -m unittest discover -s tests -v` | 43 passed | Adds: all 20 PDF sample profiles present with valid labels, paraphrased answers avoid label wording, temperature 0 for gpt-4.x only, scoring/consistency math, `evaluate --runs` end to end with a fake model, `load-samples` payloads, `sample-report` scoring, watch mode keeps running through transient errors and stops on bad keys. |
| `node tests/test_form_adapter.cjs` | Passed | Adds: `auditForm` against the 28 question titles pasted from the updated form (Oct 13), including the new speaker question and the reworded current-role question; punctuation-tolerant matching; optional vs required questions. |
| Local PostgreSQL 16 + `sql/001_tagging.sql` | Passed | `load-samples` payloads accepted by `ingest_alumni_intake` (20 pending rows, re-loading is a no-op), claim order; every block in `sql/004_samples_and_search.sql` runs. |

Still not run by me: real OpenAI calls, the user's Supabase project, a real Google Form submission
through the updated adapter. Those are steps 1-5 in `docs/THIS_WEEK.md`.
