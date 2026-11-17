# Pathways opportunity collector

From the project folder:

```sh
python3 -m alumni_tagger.pathways --limit 3
python3 -m alumni_tagger.pathways --limit 3 --write
python3 -m unittest discover -s tests -p 'test_opportunities.py'
```

The first command saves `output/pathways-preview.json`. The second imports qualifying candidates into Supabase using the existing private config. No paid source API, OpenAI call or browser automation. No new dependencies.

## Current behavior
- Starts from Pathways' approaching-deadlines listing, follows discovered program links and a bounded number of results pages. Default three detail attempts, at most two result pages; hard caps 30 attempts/five pages.
- Uses robots checks, normal TLS, a two-second minimum interval and a 24-hour HTML cache. Rejects redirects, external source-fetch URLs, oversized responses and broken detail markup.
- Extracts title, description, lead/first listed institution, application URL, explicit deadline and program end when parseable. Does not follow original institution links automatically.
- Suggests explicit topic tags in the local report, but writes empty tags so the shared Claude worker can process the listing. This is a free rules baseline, not the existing LLM alumni tagger. Research pages do not fit the alumni questionnaire prompt, so no fake alumni profiles are generated.
- Flags unknown location, remote status, deadlines, tags and provisional type. Recognizes explicit internships, or both payment and work-related language for a provisional job; ambiguous programs stay in local preview. Staff must confirm type and current application details.
- Writes only non-expired candidates with a provisional supported type, always pending. Expired and unknown-type candidates are skipped. Duplicate URL/source conflicts are checked and leave the existing row untouched; reviewed status is not reset.
- Remote is only true for explicit fully-remote language. No inferred rolling deadlines. Does not automatically activate opportunities.

## Limits
Source dates/eligibility may be stale or incomplete. Confirm at the institution's original application page before activation. Scholarships/courses can fall outside the existing job/event/volunteer schema. Multi-institution programs need staff review of organization and geography. The keyword tagger is conservative and will miss paraphrases; it does not yet satisfy a requirement for LLM opportunity tagging. The unified collection workflow runs on alternate Mondays (13:05 UTC), anchored Nov 16, 2026; the independent hourly Claude tagger consumes empty-tag rows. No ranking, digest or sending changes are included.

Source reuse permission and attribution should be checked before distributing directory descriptions. Technical feasibility does not establish a republication license.

## IntelliJ
Open the integrated checkout at `/Users/aryaprince/Downloads/technovation-integration` in IntelliJ. The old Downloads clone remains unchanged. Don't run another agent on these same files concurrently.

## Nov 12 validation
All 74 Python project tests passed, including 11 new scraper tests. Live bounded run collected three current-deadline candidates with no extraction failures. One paid research/postdoctoral candidate was imported as pending; two unknown-type programs stayed local. Replaying the same report inserted zero rows and recognized one duplicate. Supabase readback confirmed canonical Academia/Research tagging and location/remote/type review flags. This sample is a postdoctoral fellowship, so staff must verify audience fit; it is not automatically eligible for every alum or active for ranking.

No commit, GitHub push or schedule was created. The pre-existing `alumni_tagger/__main__.py` modification was left untouched.
