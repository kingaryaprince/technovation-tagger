"""Speaker Bureau search from the terminal. Calls the ranked_speakers_search / speaker_filter_options
functions created by sql/005_speaker_search.sql. Reads Supabase only; never calls OpenAI."""

CATEGORIES = (
    'industry', 'career_stage', 'project_domain', 'project_built_with', 'project_approach',
    'interests', 'skills', 'story_angles', 'speaking_types', 'audiences', 'expertise_areas',
    'story_formats', 'opportunities', 'interview_formats', 'interview_experience', 'regions',
    'speaker_interest', 'availability', 'languages', 'deep_experience', 'moderate_experience',
    'emerging_interest')

# Shorthand for the things an admin most often adds on top of a topic search (availability is
# deliberately not one of them). Each option is just
# CATEGORY=TAG under a friendlier name: "--audience Students" is the same as
# "--tag audiences=Students". Values must match the tags the worker produced, so check them with
# the speaker_filter_options list first. To support another shorthand, add a line here.
FILTER_OPTIONS = {
    'audience': ('audiences', 'who the event is for, e.g. Students'),
    'language': ('languages', 'a language the speaker is comfortable in, e.g. Spanish'),
    'experience': ('interview_experience', 'their experience hosting interviews or conversations'),
    'speaking_type': ('speaking_types', 'the kind of speaking, e.g. panelist or news media'),
}


def _parse(values):
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
    return picks


def parse_tags(values):
    """Same as before: CATEGORY=TAG strings only, and at least one is required."""
    picks = _parse(values)
    if not picks:
        raise ValueError('Pick at least one tag, e.g. --tag audiences=Students --tag skills=Python')
    if len(picks) > 50:
        raise ValueError('Pick at most 50 tags')
    return picks


def build_picks(tags=None, **filters):
    """Combine --tag CATEGORY=TAG values with the shorthand options (audience, language,
    experience, speaking_type). Each shorthand takes a list of values, so an admin
    can add as many as they like. A tag given twice is only counted once."""
    picks = _parse(tags)
    for name, values in filters.items():
        if name not in FILTER_OPTIONS:
            raise ValueError(f'Unknown filter "{name}". Use one of: ' + ', '.join(FILTER_OPTIONS))
        category = FILTER_OPTIONS[name][0]
        if isinstance(values, str):
            values = [values]
        for value in values or []:
            value = (value or '').strip()
            if not value:
                raise ValueError(f'--{name.replace("_", "-")} needs a value, e.g. --{name.replace("_", "-")} Students')
            picks.append({'category': category, 'tag': value})
    unique, seen = [], set()
    for pick in picks:
        key = (pick['category'], pick['tag'])
        if key not in seen:
            seen.add(key)
            unique.append(pick)
    if not unique:
        raise ValueError('Pick at least one tag, e.g. --tag skills=Python --audience Students --language Spanish')
    if len(unique) > 50:
        raise ValueError('Pick at most 50 tags')
    return unique


def search(db, picks, limit=5, speakers_only=True, strict=False):
    """Rank speakers by how many of the picked tags they match. With strict=True, only people who
    match every pick are returned."""
    if not 1 <= limit <= 50:
        raise ValueError('Use --limit 1-50')
    # In strict mode, ask for the most the function allows and trim afterwards, so the cut-off
    # never hides someone who matches everything.
    rows = db.rpc('ranked_speakers_search', {'p_tags': picks, 'p_limit': 50 if strict else limit,
                                             'p_speakers_only': speakers_only})
    if strict:
        full = [r for r in rows or [] if r['match_count'] == r['total_selected']][:limit]
        rows = [dict(r, rank=i) for i, r in enumerate(full, 1)]  # renumber 1, 2, 3 after filtering
    return rows


def format_results(rows, strict=False):
    if not rows:
        if strict:
            return ('No one matches every one of those tags. Drop a requirement, or leave off --strict '
                    'to rank people by how many they match.')
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


def add_filter_arguments(parser):
    """Add the new command-line options to an argparse parser: --audience, --language, --experience,
    --speaking-type (each can be repeated) and --strict."""
    for name, (category, help_text) in FILTER_OPTIONS.items():
        parser.add_argument('--' + name.replace('_', '-'), action='append', metavar='TAG',
                            help=f'Also look for this {category} tag ({help_text}). Repeat to add more.')
    parser.add_argument('--strict', action='store_true',
                        help='Only show people who match every selected tag')


def picks_from_args(args):
    """Build the tag list from parsed command-line arguments (--tag plus the options above)."""
    return build_picks(getattr(args, 'tag', None),
                       **{name: getattr(args, name, None) for name in FILTER_OPTIONS})
