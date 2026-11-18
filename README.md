> **Job listings (Nov 2026):** importing internships from GitHub job lists and tagging
> them is under [Import job listings and tag opportunities](#import-job-listings-and-tag-opportunities).
>
> **Week 7 (Oct 18): Speaker Bureau search** — start with `docs/SPEAKER_SEARCH.md`.
> Oct 13 tagging steps are still in `docs/THIS_WEEK.md`.

# Technovation: week-three alumni tagging

Start here. This is a Python prototype for the October 6 assignment: turn free-text
intake answers into queryable Supabase tags. It includes a tagging worker, examples,
offline tests, a Supabase migration and an adapter for the team's updated Google Form.

**Current status:** 30 Python tests and the form-adapter checks pass. The SQL migration
and database smoke/lease/permission checks also pass in an isolated PostgreSQL engine
(PGlite). The OpenAI call, live Supabase/PostgREST integration and real Google trigger
still need verification with your accounts. No paid model calls have been made during
preparation. See `docs/VALIDATION.md` for the precise test boundaries.

## How the tagging works

An alum fills out the Google Form. The Apps Script adapter saves their raw answers to
Supabase as a `pending` row and does nothing else; it never calls OpenAI. Tagging
happens later, when the Python worker runs:

1. **Claim.** The worker leases one pending row through `claim_alumni_tagging`, so two
   workers never tag the same response at once.
2. **Select inputs.** Only the six free-text answers the taxonomy uses (role, project,
   since Technovation, interests, skills, story) are kept. Name, email, birthdate and
   structured choices never reach the model. A profile with all six blank skips the API.
3. **Ask the model.** One OpenAI request sends those answers plus the controlled
   vocabulary from `data/taxonomy.json`. A strict JSON schema means the model can only
   pick labels that exist in the taxonomy, and every label must come with the field it
   came from and an exact quote from that field.
4. **Validate.** `core.validate` rejects the whole result if any tag is unknown or
   repeated, cites the wrong field, or has a quote that isn't actually in the answer.
   Career stage allows at most one label. Nothing is half-saved.
5. **Write back.** On success the row becomes `completed` with `tags`, `evidence`, model,
   prompt/taxonomy versions and token usage. On failure it becomes `failed` with only an
   error class name, and stays failed until someone requeues it.

If the same email submits again, the row's `revision` goes up, its old tags are cleared
and it goes back to `pending`. A worker still holding the older revision gets its write
rejected, so stale tags can't overwrite newer answers. The prompt (`core.PROMPT`) also
tells the model not to infer skills from job titles or identity from names or places;
story labels like Woman of Color in Tech need explicit self-description.

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
   `data/sample_profiles.json`, sample `01-amina`.

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

The controlled vocabulary is in `data/taxonomy.json`: the team's **Finalized Tags** list
(version `2026-10-18-finalized-v2`, prompt `v3`). It replaced the Oct 6 draft from the
brainstorming PDF; `App` and `SMS/Messaging` replaced `Mobile App`/`Web App`, `Social Impact`
was dropped, and seven labels were added. Edit this file and increment its version when the
team changes labels. Changing the file affects new work; it does not automatically
reprocess completed records (block 4 of `sql/004_samples_and_search.sql` re-queues samples).

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

## Import job listings and tag opportunities

Two tools fill the Supabase `opportunities` table with internships and keep it current:

- `job_import` copies listings from a GitHub job-list repo (default
  [SimplifyJobs/Summer2027-Internships](https://github.com/SimplifyJobs/Summer2027-Internships)).
  Every saved `url` is the employer's own posting, never a link through Simplify or
  another list's site.
- `opportunities` tags opportunities whose `tags` are still empty, using Claude.

### One-time setup

1. Put the keys in `.env.local` in this folder (Git ignores it), one `NAME=value` per line:

   ```
   SUPABASE_URL=https://YOUR-PROJECT.supabase.co
   SUPABASE_KEY=sb_secret_...
   ANTHROPIC_API_KEY=sk-ant-...
   ANTHROPIC_WORKSPACE_ID=wrkspc_...   # only for a user-level key (starts sk-ant-usr-)
   ANTHROPIC_MODEL=claude-opus-5-5     # optional; claude-haiku-5-5 is much cheaper
   ```

   The commands read `.env.local` themselves; there's nothing to `source` or `export`.
2. Install the Claude SDK (only the tagger needs it): `pip3 install anthropic`
3. On a Mac with Python from python.org, if you see `CERTIFICATE_VERIFY_FAILED`, run
   `open "/Applications/Python 3.11/Install Certificates.command"` once (use your Python
   version), or `export SSL_CERT_FILE=/etc/ssl/cert.pem` in each terminal.
4. Run `sql/009_internship_type.sql` once in the Supabase SQL editor. It adds
   `internship` to the table's allowed `type` values without dropping existing ones.
5. Run `sql/010_date_posted.sql` once. It adds a `date_posted` column (the day the list
   says the job was posted) and fills it in for rows already imported from SimplifyJobs and
   other `listings.json` lists. README-table lists have no reliable dates, so theirs stay empty.

### Import listings

```bash
python3 -m alumni_tagger.job_import --dry-run            # preview counts and 5 rows; writes nothing
python3 -m alumni_tagger.job_import --limit 50           # add the 50 newest listings not imported yet
python3 -m alumni_tagger.job_import                      # add every open listing
python3 -m alumni_tagger.job_import --status active      # new rows go live without review
python3 -m alumni_tagger.job_import --max-age-days 60    # only what Simplify's README shows
```

Re-running is safe. Each run adds only listings not imported before, refreshes the text of
ones the list edited, and sets `status = 'expired'` on ones the list closed. Existing rows
keep their `status` and `tags`. New rows are `pending` unless you pass `--status active`.
To make pending imports live, run in the Supabase SQL editor (change the source to match):

```sql
update opportunities set status = 'active'
where source = 'simplify_summer2027' and status = 'pending';
```

### Switch to a different job list

Pass `--repo owner/name` for any public GitHub repo:

```bash
python3 -m alumni_tagger.job_import --repo SimplifyJobs/Summer2028-Internships --dry-run
python3 -m alumni_tagger.job_import --repo vanshb03/Summer2027-Internships --limit 50
python3 -m alumni_tagger.job_import --repo negarprh/Canadian-Tech-Internships-2027
python3 -m alumni_tagger.job_import --repo SimplifyJobs/New-Grad-Positions
```

Always start with `--dry-run`: it shows how many listings were found and how many were
skipped. The rest is worked out from the repo:

| What | How |
| --- | --- |
| Format | `.github/scripts/listings.json` if the repo has one (Simplify's format, also used by vanshb03); otherwise the README table with Company, Role, Location and an apply link. A 🔒 row counts as closed and `↳` repeats the company above. Force one with `--format listings` or `--format readme`. |
| Term | From the repo name: `Summer2028-Internships` keeps only "Summer 2028" listings. A name without a season and year keeps everything. Override with `--term "Fall 2026"`, or `--term ""` for all. Data-file repos only; README tables aren't filtered by term. |
| `type` | `internship` if the repo name contains "Intern", otherwise `job`. |
| `source` | `simplify_<term>` for SimplifyJobs repos, otherwise `<owner>_<term or repo name>`, e.g. `vanshb03_summer2027`. Each repo's rows are tracked and expired separately, so lists never overwrite each other. The same job on two lists appears twice. |
| Links | Tracking parameters are removed. Links through a shortener (`go.vansh.nyc`, `bit.ly`, ...) are followed to the employer's page when the listing is first added. Links that only lead to a list's own site are skipped. |

Lists checked in November 2026:

| Repo | Works? |
| --- | --- |
| SimplifyJobs/Summer2027-Internships, SimplifyJobs/New-Grad-Positions | Yes (data file) |
| vanshb03/Summer2027-Internships, vanshb03/New-Grad-2027 | Yes (data file; links resolved through go.vansh.nyc) |
| negarprh/Canadian-Tech-Internships-2027 | Yes (README table) |
| zshah101/Automated-List-Of-Summer-2027-and-Fall-2026-Tech-Internships | Yes (README table) |
| zapplyjobs/* | No: every link goes through zapply.jobs, which hides the employer's link |
| northwesternfintech/2027QuantInternships | No: not a Company/Role table; the run stops without changing anything |

### Tag opportunities

```bash
python3 -m alumni_tagger.opportunities --limit 3    # small test first
python3 -m alumni_tagger.opportunities --limit 50
```

It tags rows whose `tags` are empty and whose `status` isn't `expired`, newest first.
Tags must come from `data/taxonomy.json` and quote the listing word for word; a row that
fails stays untagged and is retried on the next run. Alumni tagging still uses OpenAI.

### Run it automatically on GitHub

Two workflows in `.github/workflows/` run from the `main` branch once merged:

| Workflow | When | Needs these repository secrets |
| --- | --- | --- |
| Import job listings (`import-job-listings.yml`) | Daily at 13:05 UTC, adding every new listing as `pending`. Also manual: Actions → Run workflow (default 50, with repo and status options). | `SUPABASE_URL`, `SUPABASE_KEY` |
| Autotag opportunities (`autotag-opportunities.yml`) | Hourly, 25 rows per run | `SUPABASE_URL`, `SUPABASE_KEY`, `ANTHROPIC_API_KEY`, plus `ANTHROPIC_WORKSPACE_ID` for a user-level key; optional `ANTHROPIC_MODEL` |

Daily import settings are repository **variables** (Settings → Secrets and variables →
Actions → Variables tab). None exist by default, and none are needed:

| Variable | Effect |
| --- | --- |
| `JOB_IMPORT_ENABLED` | `false` skips the daily run; manual runs still work. Set `true` or delete it to turn it back on. CLI: `gh variable set JOB_IMPORT_ENABLED --body false` |
| `JOB_LIST_REPO` | Which repo the daily run imports, e.g. `SimplifyJobs/Summer2028-Internships`. Default `SimplifyJobs/Summer2027-Internships`. CLI: `gh variable set JOB_LIST_REPO --body owner/name` |

The daily run imports one repo. GitHub pauses scheduled workflows after 60 days with no
commits to a public repo; turn it back on from the Actions tab.

## Troubleshooting

| Symptom | Next action |
| --- | --- |
| No new raw row | Apps Script → Executions. Confirm an `onUpdatedFormSubmit` trigger run and inspect its HTTP status. Run `auditForm` again. |
| Handler says it needs an event | Submit the actual form; do not click Run on `onUpdatedFormSubmit`. |
| HTTP 401/403 | Check the API key and its permissions. For Supabase use the server-side key. |
| HTTP 404 | Check migration/table/RPC existence, project URL or OpenAI model availability. |
| OpenAI 429 | Check API billing/quota/rate limit. Fix it, then requeue the failed test row. |
| `failed`, `ValidationError` | Review the input and run a local `tag` command to reproduce without changing the DB. Review any mismatched quote or unsupported output. |
| Claude 400 "not scoped to a workspace" | The key is user-level (`sk-ant-usr-`). Add `ANTHROPIC_WORKSPACE_ID=wrkspc_...` to `.env.local`, or use a workspace key (`sk-ant-api03-`). |
| Import says "No job table found" | That repo's README has no table with Company and Role columns. It isn't supported; nothing was changed. |
| Import finds 0 open, all skipped | The list's links all go through its own site (like zapply.jobs), so there's no employer link to save. |
| Supabase 400 on import with `opportunities_type_check` | Run `sql/009_internship_type.sql`. |
| Supabase 400 on import mentioning `date_posted` | Run `sql/010_date_posted.sql`. |
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
- `alumni_tagger/job_import.py`: imports listings from GitHub job-list repos.
- `alumni_tagger/opportunities.py`: tags opportunities with Claude.
- `.github/workflows/`: daily import and hourly opportunity tagging.
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
