# Week of Oct 13: form → tags → Supabase, plus the full sample run

Action items from Meeting 6:
1. A Google Form response is automatically tagged and stored in Supabase.
2. Run the tagger on the full set of sample alumni profiles and check that tags are correct and consistent.

Where each step happens:
- **Terminal** = the IntelliJ terminal, inside the `technovation-tagger` folder.
- **SQL** = Supabase > alumni-hub > SQL Editor.
- **Apps Script** = script.google.com.

## 0. Install this version (Terminal)

Unzip the new package, then copy your `config.local.json` from the old folder into the new
`technovation-tagger` folder (that file holds your keys; it is not in the zip). Open the new
folder in IntelliJ and run:

```bash
python3 -m pip install --upgrade certifi
export SSL_CERT_FILE="$(python3 -m certifi)"
python3 -m alumni_tagger check-db
```

Expect: `Supabase intake table is reachable.` Run the `export` line again in every new terminal.

## 1. Full sample run + consistency check (Terminal, no database writes)

```bash
python3 -m alumni_tagger evaluate --runs 2
```

Tags all 20 PDF sample profiles plus 5 edge cases twice each (about 50 OpenAI calls, a few
cents, a few minutes). It prints a summary and saves `output/evaluation.md` (readable) and
`output/evaluation.json` (full detail with evidence quotes). The `.md` file is what you show
the team: precision/recall per category, the most-missed tags, and any profile whose tags
changed between the two runs.

How to read the misses: some are real tagger mistakes, some are places where the team's sample
tags assume something the answer doesn't say. Example: almost every sample project says "an app"
and the PDF tags it Mobile App, but the tagger is told not to guess the platform. Another:
"Senior Consultant" is tagged Experienced (5+ yrs) in the PDF, but the tagger only picks a
years band when years are stated. Those are team decisions, not code bugs.

## 2. Connect the updated Google Form (Apps Script, about 10 min)

You need **edit access** to the team form. Its ID (from the edit link) is
`1lxCd_tKVcKGqABkogtqGMc1Xgy5c2Frf9EuE0KEVbAA`.

1. Go to script.google.com while signed in with the Google account that can edit the form.
   Click **New project**, delete the starter code, paste all of `apps_script/updated_form_to_supabase.gs`, and save.
2. Gear icon > **Script Properties**, add three:
   - `FORM_ID` = `1lxCd_tKVcKGqABkogtqGMc1Xgy5c2Frf9EuE0KEVbAA`
   - `SUPABASE_URL` = `https://oyldvofbbdhqepdnnylt.supabase.co`
   - `SUPABASE_KEY` = your `sb_secret_...` key
3. Choose `auditForm` in the function dropdown, click **Run**, approve permissions.
   Expect `Matched 28 of 28 questions.` If a required question is reported missing, someone
   renamed it in the form: send me the log and I'll update the mapping.
4. Choose `setupUpdatedForm`, click **Run**. Expect `Trigger ready. Respondent link: ...`

From now on every submission is saved to `alumni_intake_responses` as `pending` within seconds.

## 3. Start the tagger (Terminal, leave it open)

```bash
python3 -m alumni_tagger worker --watch
```

It checks for new responses every 15 seconds and tags them as they arrive, until you press
Ctrl+C. It only runs while this terminal and your laptop are on: responses submitted while it
is off stay `pending` and get tagged the next time you start it. Nothing is lost.

## 4. Prove it end to end (Form + Terminal + SQL)

**Start recording here** if you are recording for the meeting.

1. Open the respondent link from step 2.4. Submit a response with a fake email such as
   `demo-oct13@example.com` and natural answers, e.g. interests "I'd love to help younger girls
   learn to code and get into tech", other skills "I give talks at school assemblies".
2. Within about 15 seconds the terminal prints `{"completed": 1, ...}`.
3. In SQL, run block 1 of `sql/004_samples_and_search.sql`: the row shows `completed`.
4. Run block 3b or 3d. The demo row shows up under labels like Mentorship / Women in STEM /
   Public Speaking that it never typed literally.

**Stop recording.**

## 5. Put the 20 samples through the real system (Terminal + SQL)

With the worker from step 3 still running, open a second terminal tab (run the `export` line there too):

```bash
python3 -m alumni_tagger load-samples
```

The watching worker picks up all 20 within a minute or two. Then:

```bash
python3 -m alumni_tagger sample-report
```

This scores the tags actually saved in Supabase against the PDF's expected tags and writes
`output/supabase-sample-report.md`. SQL blocks 2 and 3a-3c now return real-looking results for a demo.

Before the pilot, run SQL block 5 to delete the sample rows (only `sample-XX@example.com` emails).

## What's not done yet

- The worker isn't hosted, so tagging only happens while your laptop runs it.
  Simplest next step is a scheduled GitHub Action that runs `worker` every few minutes.
- The architecture diagram says "Claude API". If the PMs' API key turns out to be an
  Anthropic key, the OpenAI call in `clients.py` needs a Claude version (small change).
- New rows go to `alumni_intake_responses`, not the original `alumni_profiles` table.
  Merging the two is a team schema decision.
