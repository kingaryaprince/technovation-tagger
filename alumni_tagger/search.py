"""Speaker Bureau search from the terminal. Calls the ranked_speakers_search / speaker_filter_options
functions created by sql/005_speaker_search.sql. Reads Supabase only; never calls OpenAI."""

CATEGORIES = (
    'industry', 'career_stage', 'project_domain', 'project_built_with', 'project_approach',
    'interests', 'skills', 'story_angles', 'speaking_types', 'audiences', 'expertise_areas',
    'story_formats', 'opportunities', 'interview_formats', 'interview_experience', 'regions',
    'speaker_interest', 'availability', 'languages', 'deep_experience', 'moderate_experience',
    'emerging_interest')


def parse_tags(values):
    """Turn ["audiences=Students", "skills=Python"] into [{"category":..., "tag":...}]."""
    picks = []
    for value in values or []:
        category, sep, tag = value.partition('=')
        category, tag = category.strip(), tag.strip()
        if not sep or not category or not tag:
            raise ValueError(f'Write each tag as CATEGORY=TAG, e.g. audiences=Students (got "{value}")')
        if category not in CATEGORIES:
            raise ValueError(f'Unknown category "{category}". Use one of: ' + ', '.join(CATEGORIES))
        picks.append({'category': category, 'tag': tag})
    if not picks:
        raise ValueError('Pick at least one tag, e.g. --tag audiences=Students --tag skills=Python')
    if len(picks) > 50:
        raise ValueError('Pick at most 50 tags')
    return picks


def search(db, picks, limit=5, speakers_only=True):
    if not 1 <= limit <= 50:
        raise ValueError('Use --limit 1-50')
    return db.rpc('ranked_speakers_search', {'p_tags': picks, 'p_limit': limit,
                                             'p_speakers_only': speakers_only})


def format_results(rows):
    if not rows:
        return 'No one matches any of those tags. Try fewer tags, or --all to include non-speakers.'
    lines = []
    for r in rows:
        missing = ', '.join(t['tag'] for t in r.get('missing_tags') or [])
        lines.append(f"{r['rank']}. {r['full_name']}  {r['match_count']}/{r['total_selected']} tags"
                     f"  ({r.get('availability') or 'availability not given'})")
        if missing:
            lines.append(f'   missing: {missing}')
    return '\n'.join(lines)


def format_options(rows, category=None):
    if category and category not in CATEGORIES:
        raise ValueError(f'Unknown category "{category}". Use one of: ' + ', '.join(CATEGORIES))
    rows = [r for r in rows if not category or r['category'] == category]
    if not rows:
        return 'No tags yet. Load responses (or the samples) and run the worker first.'
    lines, current = [], None
    for r in rows:
        if r['category'] != current:
            current = r['category']
            lines.append(('' if not lines else '\n') + current)
        lines.append(f"  {r['tag']} ({r['people']})")
    return '\n'.join(lines)
