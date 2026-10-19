-- Speaker search examples for the Supabase SQL Editor. Run ONE block at a time.
-- Needs 005_speaker_search.sql and the sample profiles from 008_replace_sample_profiles.sql. Read-only.
-- Every call passes all three arguments (tags::jsonb, how many people, speakers only).
-- "Expected" = what the 20 sample profiles return. Real form responses can show up too.

-- 1. Healthcare expert for an in-person student panel
--    Expected: Lerato Mokoena 4/4, Rebecca Castillo 4/4,
--              then Denise Whitfield, Mei-Ling Tan, Tamika Brooks at 3/4 (missing Healthcare)
select rank, full_name, match_count || '/' || total_selected as score, availability, missing_tags
from public.ranked_speakers_search('[
  {"category": "expertise_areas", "tag": "Healthcare"},
  {"category": "audiences",       "tag": "Students"},
  {"category": "speaking_types",  "tag": "Speak as a panelist"},
  {"category": "speaking_types",  "tag": "In-Person Events"}
]'::jsonb, 5, true);

-- 2. Available now, comfortable with parents, cares about Women in STEM
--    Expected: Wanjiru Kamau 3/3, then Camila Herrera, Denise Whitfield, Mei-Ling Tan, Sofia Mendes at 2/3
select rank, full_name, match_count || '/' || total_selected as score, availability
from public.ranked_speakers_search('[
  {"category": "availability", "tag": "Available now"},
  {"category": "audiences",    "tag": "Parents"},
  {"category": "interests",    "tag": "Women in STEM"}
]'::jsonb, 5, true);

-- 3. A story-driven talk, top 3 only
--    Expected: Nneka Eze 2/2, Zara Ahmed 2/2, Denise Whitfield 1/2
select rank, full_name, match_count || '/' || total_selected as score
from public.ranked_speakers_search('[
  {"category": "story_angles", "tag": "Immigration & Visa Navigation"},
  {"category": "story_angles", "tag": "Woman of Color in Tech"}
]'::jsonb, 3, true);

-- 4. Deeply experienced in leadership, speaks Spanish (matching ignores upper/lower case)
--    Expected: Rebecca Castillo 2/2, then Camila Herrera, Mei-Ling Tan, Sofia Mendes, Jasmine Park at 1/2
select rank, full_name, match_count || '/' || total_selected as score, matched_tags
from public.ranked_speakers_search('[
  {"category": "deep_experience", "tag": "Leadership"},
  {"category": "languages",       "tag": "Spanish"}
]'::jsonb, 5, true);

-- 5. Include people who aren't interested in speaking (third argument false)
--    Expected: Dilnoza Karimova. With true instead, no one: she chose "Not Interested".
select rank, full_name, match_count
from public.ranked_speakers_search('[{"category": "interests", "tag": "Cybersecurity"}]'::jsonb, 5, false);

-- 6. Every tag people actually have, with head counts (what the admin UI's filters show)
select * from public.speaker_filter_options();

-- 7. All tags for one person, LLM and form answers together (sample-07 = Ananya Iyer)
select s.category, s.tag
from public.speaker_search_tags s
join public.alumni_intake_responses r on r.id = s.response_id
where r.email = 'sample-07@example.com'
order by s.category, s.tag;
