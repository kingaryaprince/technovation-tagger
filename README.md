> **This week (Oct 13):** start with `docs/THIS_WEEK.md`.

# Technovation: week-three alumni tagging

Start here. This is a Python prototype for the October 6 assignment: turn free-text
intake answers into queryable Supabase tags. It includes a tagging worker, examples,
offline tests, a Supabase migration and an adapter for the team's updated Google Form.

**Current status:** 30 Python tests and the form-adapter checks pass. The SQL migration
and database smoke/lease/permission checks also pass in an isolated PostgreSQL engine
(PGlite). The OpenAI call, live Supabase/PostgREST integration and real Google trigger
still need verification with your accounts. No paid model calls have been made during
preparation. See `docs/VALIDATION.md` for the precise test boundaries.

## First: run one sample on your computer

1. Extract this ZIP. Open the `technovation-tagger` folder in IntelliJ or any editor.
   Use its Terminal tab. Python 3.10 or newer is required. No pip packages are required.
   If IntelliJ's Python support is unavailable, its terminal still works.
2. In that folder, run:

   ```bash
   python3 --version
   python3 -m unittest discover -s tests -v
   python3 -m alumni_tagger configure
   ```

   On Windows, use `py` in place of `python3` if needed.

3. The configuration prompts ask for:

   | Setting | What to enter |
   | --- | --- |
   | `OPENAI_API_KEY` | Your personal OpenAI **API** key. The prompt hides typing. |
   | `OPENAI_MODEL` | Press Enter to use `gpt-4.1-mini`. It is configurable. |
   | `SUPABASE_URL` | Can be blank for the first local demo. |
   | `SUPABASE_KEY` | Can be blank for the first local demo. |

   Values go in `config.local.json`, ignored by Git. No key goes in source code,
   screenshots, chats, or a ZIP shared with teammates. Environment variables with
   the same names override this file. An API key/account with available API billing
   is needed; a ChatGPT login or subscription is not an API key.

4. Run one paid OpenAI request:

   ```bash
   python3 -m alumni_tagger demo
   ```

   It prints tags and saves `output/demo.json`, including evidence quotes and token
   usage. It makes **no database changes**. Check the tags against
   `data/sample_profiles.json`, sample `01-amara`.

5. Evaluate more samples after the first works:

   ```bash
   python3 -m alumni_tagger evaluate --limit 3
   python3 -m alumni_tagger evaluate --runs 2
   ```

   All 20 sample profiles from the Questionnaire Brainstorming PDF are followed by five
   edge cases. `--runs 2` tags each twice and reports any tags that changed between runs.
   A readable summary is saved next to the JSON as `output/evaluation.md`. Blank or
   wholly irrelevant profiles skip the API. Other profiles make one request, with
   at most one retry for HTTP 429 or selected server errors. Review missing/extra
   tags in the JSON report. These results measure only the annotated categories.
   They are not a production-quality accuracy estimate.

## Then: connect Supabase

The live `alumni_profiles` schema was not provided. This package therefore creates
one separate table, **`alumni_intake_responses`**. The updated form writes here,
and the worker adds tags here. Last week's `alumni_profiles` table is untouched.
This table is an integration staging choice, not a replacement for the agreed
seven-table hub architecture. A later join/migration needs the actual profile schema.

1. Open your `alumni-hub` project in Supabase, then SQL Editor.
2. Run all of `sql/001_tagging.sql` **once**. It runs in one transaction. If the table
   already exists, stop and inspect it; do not delete it or blindly rerun the migration.
3. Run `tests/database_smoke.sql`. It tests duplicate ingestion, stale results,
   out-of-order arrivals, completion and permission restrictions. Its test writes
   are rolled back. A successful run reports a passing notice.
4. Run `python3 -m alumni_tagger configure` again. Keep the OpenAI settings and add
   your project URL and server-side secret key. Your existing working Apps Script
   uses these two values already. Do not use the publishable/anonymous key.
5. Run:

   ```bash
   python3 -m alumni_tagger check-db
   ```

All table/RPC access is restricted to `service_role`. There are no browser-facing
read policies. Later staff access needs a deliberate authorization design.

## Connect the updated Google Form

You need edit access to the team's actual updated form. This adapter attaches to
that form and never creates another form or changes its questions.

1. Make a **new standalone Apps Script project** so last week's working script remains
   available. Paste `apps_script/updated_form_to_supabase.gs` into it.
2. Add these Script Properties in Project Settings:

   | Property | Value |
   | --- | --- |
   | `FORM_ID` | From the **editor** URL: `https://docs.google.com/forms/d/FORM_ID/edit`. Do not use the `/d/e/.../viewform` public ID. |
   | `SUPABASE_URL` | Same project URL as your Python configuration. |
   | `SUPABASE_KEY` | Server-side secret key. Script editors can access it, so limit editor access to trusted maintainers. |

3. Select and run `auditForm`. It checks the actual question titles and the six
   free-text types used for tagging. Minor spacing, capitalization and curly
   apostrophes are normalized. If questions differ, it reports them; adjust
   `INTAKE_FIELDS` to match the actual titles and rerun. The copied form text did
   not include every choice option, so the adapter preserves actual selected
   values without guessing option lists.
4. Run `setupUpdatedForm` and authorize the requested form/network/trigger access.
   It installs one form-submit trigger owned by the account running setup.
   Only one team member should install this trigger; reruns remove only that
   user's matching trigger in this project.
5. Open the respondent form and submit **fake** test details, such as
   `test1@example.com`. Include a role, project, interests and skills.
6. Check `alumni_intake_responses` in Supabase. `tagging_status` should be `pending`.
   Inspect `raw_answers`, including checkbox arrays and the two named-row grids.

The script preserves all returned questions in `_original_items` as well as
mapping known fields. It does not backfill earlier submissions. Submit a new test
after setup. For an individual failed response, set `RETRY_RESPONSE_ID` to its Google
Form response ID and run `retryOneResponse`.

## Run the background worker

With a pending test response in Supabase:

```bash
python3 -m alumni_tagger worker --limit 1
```

Refresh the table. The row should become `completed`, with `tags`, `evidence`,
`model`, versions, token usage and `tagged_at` populated. Run queries from
`sql/002_demo_queries.sql` to find profiles by skills or interests.

For a short demo where the worker waits for new submissions:

```bash
python3 -m alumni_tagger worker --watch --limit 5
```

This checks every 15 seconds and stops after **five total claimed jobs**, including
failed or stale jobs. Ctrl+C stops it early. It runs only while your terminal and
computer are running. A hosted scheduler/worker is a later deployment step, not
included or already running. A scheduled execution can use `worker --limit 5`.

The intake script never calls OpenAI. It saves the response first. The Python process
claims one row, tags it, validates output and writes back. Failed tagging does not
erase the intake. Failed jobs require review and explicit requeue with
`sql/003_retry_one.sql`; they do not consume credits in an endless retry loop.

## What gets tagged

| Output category | Source answer(s) |
| --- | --- |
| `industry` | Current role / organization / study |
| `career_stage` | Current role plus what they have done since Technovation |
| `project_domain` | Technovation project description |
| `project_built_with` | Technovation project description |
| `project_approach` | Technovation project description |
| `interests` | Topics or causes of interest |
| `skills` | Other skills |
| `story_angles` | Life/professional experience answer only |

The controlled vocabulary is in `data/taxonomy.json`, based on pages 3–4 of the
brainstorming PDF. That section is more complete than the later overlapping lists;
the choice is documented and **still needs team confirmation**. Edit this file and
increment its version when the team finalizes labels. Changing the file affects
new work; it does not automatically reprocess completed records.

Only six free-text fields are sent to OpenAI. Names, email, birthdate, addresses,
language selections and the complete raw response object are excluded. If an alum
includes personal details inside one of those six answers, those details remain
in the model input. `store=false` disables Responses storage; it is not a claim of
zero provider retention. Use synthetic examples during initial testing.

Structured choices such as languages, audiences, availability and topic familiarity
are preserved, not inferred by the LLM. Birthdate remains raw; age is not inferred.
Every generated tag must carry a verbatim quote from its allowed field. This checks
traceability, not semantic truth. Human review and live evaluation still matter,
especially for negation, aspirations and identity-related story labels. Story identity
labels require explicit self-description; the model must not infer them from names,
origin, languages or participation in Technovation.

## Demonstrate completion at the meeting

1. Show a fake updated-form submission arriving as `pending` in Supabase.
2. Run the worker and show it becomes `completed` with understandable tags.
3. Show evidence for two tags and run a query for `Python` or `AgTech`.
4. Submit the same fake email with a changed skill or interest. The same row should
   become pending, increase its revision and clear its old tags. Run the worker again.
5. Show the live evaluation report, including errors and limitations.
6. Confirm the final tag vocabulary with Kayden/Pragya and agree how to merge the
   staging intake into the existing profile schema with Junnie.

## Troubleshooting

| Symptom | Next action |
| --- | --- |
| No new raw row | Apps Script → Executions. Confirm an `onUpdatedFormSubmit` trigger run and inspect its HTTP status. Run `auditForm` again. |
| Handler says it needs an event | Submit the actual form; do not click Run on `onUpdatedFormSubmit`. |
| HTTP 401/403 | Check the API key and its permissions. For Supabase use the server-side key. |
| HTTP 404 | Check migration/table/RPC existence, project URL or OpenAI model availability. |
| OpenAI 429 | Check API billing/quota/rate limit. Fix it, then requeue the failed test row. |
| `failed`, `ValidationError` | Review the input and run a local `tag` command to reproduce without changing the DB. Review any mismatched quote or unsupported output. |
| `processing` after terminal closes | Lease expires after 10 minutes. Another worker run can reclaim it; three interrupted claims move it to failed. |
| Same email changed but old tags appear | The new submission must reach this table. Check its `revision` and `submitted_at`. Completed-query examples exclude pending rows. |

For custom local input, create a JSON file with keys such as `skills`, `interests`,
`project` and `current_role`, then run:

```bash
python3 -m alumni_tagger tag my_answers.json --out output/my_tags.json
```

Never share `config.local.json`. Share the code, sanitized outputs and test findings.
Use a separate team API key before a real pilot. Form-provided email is an identifier,
not verified identity; public profile updates need authentication before production.

## Files and references

- `alumni_tagger/core.py`: input selection, prompt, schema, validation.
- `alumni_tagger/clients.py`: OpenAI Responses and Supabase REST calls.
- `alumni_tagger/worker.py`: background job processing.
- `alumni_tagger/__main__.py`: command-line entry point.
- `data/`: taxonomy, full form question mapping, sample/edge-case inputs.
- `sql/`: additive migration, search examples, explicit retry.
- `apps_script/`: updated-form intake adapter.
- `tests/`: offline Python tests, optional Node adapter test, database smoke test.
- `docs/PLAN.md`: implementation decisions and integration boundaries.

Source requirements: October 6 Meeting 5 notes (pages 3–4); Technovation
Questionaire Brainstorming (pages 3–4 and 7–9); updated form text supplied in chat.

Official references checked during implementation:
- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [GPT-4.1 mini model](https://developers.openai.com/api/docs/models/gpt-4.1-mini)
- [Supabase row-level security](https://supabase.com/docs/guides/database/postgres/row-level-security)
- [Google Forms ItemResponse, including grid response ordering](https://developers.google.com/apps-script/reference/forms/item-response)
