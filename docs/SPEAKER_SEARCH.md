# Week 7: Speaker Bureau search

Goal from Meeting 7: admins pick tags and get the speakers who have them, **top 5, the person
matching the most tags first**. Natural-language search waits until Week 8.

- **SQL** = Supabase > alumni-hub > SQL Editor.
- **Terminal** = the IntelliJ terminal in the `technovation-tagger` folder (run the `export SSL_CERT_FILE=...` line from `THIS_WEEK.md` first).

The function is `ranked_speakers_search` (a teammate's draft `search_speakers` also exists in the project).

## How it ranks

1. Count how many of the picked tags each person has. Most matches first.
2. Ties: whoever is available soonest (Available now > Available soon > Uncertain timing), then by name.
3. People matching none of the picked tags are left out.
4. By default only people who picked at least one speaking type (anything except "Not Interested"),
   or answered yes to the speaker question, are included.

It searches both kinds of tags on each `alumni_intake_responses` row together:

| From | Categories |
| --- | --- |
| LLM worker (`tags`) | `industry`, `career_stage`, `project_domain`, `project_built_with`, `project_approach`, `interests`, `skills`, `story_angles` |
| Form checkboxes/selects (`raw_answers`) | `speaking_types`, `audiences`, `expertise_areas`, `availability`, `regions`, `interview_experience`, `interview_formats`, `story_formats`, `opportunities`, `speaker_interest`, `languages` |
| Familiarity grid | `deep_experience`, `moderate_experience`, `emerging_interest` (tag = topic, e.g. `Leadership`) |

Tags are the exact option text from the form (e.g. `Speak as a panelist`, `In-Person Events`), except
availability, which is shortened to `Available now` / `Available soon` / `Uncertain timing`.
Matching ignores upper/lower case. LLM tags only count once a row is `completed`.

## 1. Set it up (SQL, about 5 minutes, once)

1. Run all of `sql/005_speaker_search.sql`. It creates one view and two functions; it doesn't change any rows. Safe to re-run.
2. Run all of `sql/008_replace_sample_profiles.sql` (the current 20 sample profiles, tags from the
   finalized list). It should show `20 | 20` and the new names. These are the same people and
   answers as `data/sample_profiles.json`, tagged with the team's expected tags.
   - `load-samples` sends the same answers, so it doesn't change anyone's answers. It does
     re-queue them as `pending` so the LLM worker tags them for real (then `sample-report`
     compares the LLM's tags with the expected ones). Until the worker runs, those samples
     have no LLM tags in searches. Run 008 again to put the expected tags back.
   *(Older step, kept for reference)* `sql/006_sample_speaker_answers.sql` The 20 samples only had free-text answers, so this
   gives them availability, audiences, speaking types and the rest. The last line should show `20 | 20 | 20`.
   - Samples already in the table keep their LLM tags (no re-tagging, no OpenAI cost).
   - Samples that were never loaded are added with the PDF's expected tags, marked
     `model = 'expected-tags (no LLM call)'`. Block 4 of `004_samples_and_search.sql` + the worker replaces them with real LLM tags.
3. Run `tests/speaker_search_smoke.sql`. Expect `Speaker search smoke tests passed`. Its test rows roll back.

## 2. Try it (SQL)

Run the blocks in `sql/007_speaker_search_examples.sql` one at a time. Block 1 should return
Lerato Mokoena and Rebecca Castillo at 4/4, then Denise Whitfield, Mei-Ling Tan and Tamika Brooks at 3/4.

```sql
select rank, full_name, match_count || '/' || total_selected as score, availability, missing_tags
from public.ranked_speakers_search('[
  {"category": "audiences", "tag": "Students"},
  {"category": "expertise_areas", "tag": "Healthcare"}
]'::jsonb, 5, true);
-- 2nd argument: how many people (default 5, max 50)
-- 3rd argument: false = include people who aren't interested in speaking
-- Pass all three: with only the tags, Postgres can't choose if another ranked_speakers_search exists.
```

## 3. Try it (Terminal, no OpenAI call)

```bash
python3 -m alumni_tagger search --tag expertise_areas=Healthcare --tag audiences=Students \
  --tag "speaking_types=Speak as a panelist" --tag "speaking_types=In-Person Events"
python3 -m alumni_tagger search --tag availability="Available now" --tag audiences=Parents --limit 3
python3 -m alumni_tagger search --tag interests=Hardware/Robotics --all     # include non-speakers
python3 -m alumni_tagger search --tag skills=Python --json                  # full details
python3 -m alumni_tagger filter-options --category speaking_types            # what tags exist
```

## What each result contains

`rank`, `response_id`, `full_name`, `email`, `match_count`, `total_selected`, `match_percent`,
`matched_tags`, `missing_tags` (picked tags this person lacks, useful to show in the UI),
`availability`, and `profile`: role, location, timezone, languages, years, project, since
Technovation, speaking types, audiences, regions and all LLM tags. Birthdate is not returned.

## Access

Same rule as the rest of this repo: only the server-side secret key (`service_role`) can run
`ranked_speakers_search`, `speaker_filter_options` or read `speaker_search_tags`. Signed-in users and the
public get "permission denied". The admin UI must call it from server code:
`examples/admin_speaker_search_route.ts` is a Next.js route that checks the caller is in the
`staff` table first (change the table/column names where it says ADJUST).

## Not done yet

- Admin UI wired to this (the Claude mock-ups are the design; the route example is the glue).
- Weighting some tags more than others; Week 8 natural-language search.
- The search has no tag list of its own, only category names, so it follows
  `data/taxonomy.json` automatically when labels change.
