"""Speaker Bureau search from the terminal. Calls the ranked_speakers_search / speaker_filter_options
functions created by sql/005_speaker_search.sql. Reads Supabase only; never calls OpenAI.
The only other website it talks to is OpenStreetMap's free place lookup, for the travel radius."""
import json
import os
import time
import urllib.parse
import urllib.request

DEFAULT_RADIUS_MILES = 100   # used when --near is given without --radius
MAX_RADIUS_MILES = 5000
GEOCODER_URL = 'https://nominatim.openstreetmap.org/search'
# OpenStreetMap asks every app to identify itself. Set GEOCODER_USER_AGENT to
# "YourApp/1.0 (you@example.com)" if lookups are refused.
GEOCODER_USER_AGENT = 'TechnovationAlumniHub-speaker-search/1.0'

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


def build_picks(tags=None, allow_empty=False, **filters):
    """Combine --tag CATEGORY=TAG values with the shorthand options (audience, language,
    experience, speaking_type). Each shorthand takes a list of values, so an admin
    can add as many as they like. A tag given twice is only counted once. With allow_empty=True (used
    when a --near place is given) it is fine to pick no tags at all."""
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
    if not unique and not allow_empty:
        raise ValueError('Pick at least one tag, e.g. --tag skills=Python --audience Students --language Spanish')
    if len(unique) > 50:
        raise ValueError('Pick at most 50 tags')
    return unique


def search(db, picks, limit=5, speakers_only=True, strict=False, near=None):
    """Rank speakers by how many of the picked tags they match. With strict=True, only people who
    match every pick are returned. `near` (from near_from_args) adds the travel radius: being within
    it counts as one extra match, and with near['include_virtual'] False only people within it are
    listed (so with strict=True, everyone returned is also within it)."""
    if not 1 <= limit <= 50:
        raise ValueError('Use --limit 1-50')
    # In strict mode, ask for the most the function allows and trim afterwards, so the cut-off
    # never hides someone who matches everything.
    params = {'p_tags': picks, 'p_limit': 50 if strict else limit, 'p_speakers_only': speakers_only}
    if near:
        params.update({'p_lat': near['lat'], 'p_lng': near['lng'], 'p_radius_miles': near['radius'],
                       'p_place_label': near['label'], 'p_include_virtual': near['include_virtual']})
    rows = db.rpc('ranked_speakers_search', params)
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
        away = f"  ({r['distance_miles']} mi away)" if r.get('distance_miles') is not None else ''
        lines.append(f"{r['rank']}. {r['full_name']}  {r['match_count']}/{r['total_selected']} tags{away}")
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
    parser.add_argument('--near', metavar='PLACE',
                        help='Travel radius: rank people who live within --radius miles of this place higher, '
                             'e.g. --near "Detroit, MI, USA"')
    parser.add_argument('--radius', type=float, metavar='MILES',
                        help=f'How far from --near counts as close (default {DEFAULT_RADIUS_MILES} miles)')
    parser.add_argument('--no-virtual', action='store_true',
                        help='With --near: only list people within the radius (leave off to also list '
                             'virtual speakers from farther away, ranked lower)')


def picks_from_args(args):
    """Build the tag list from parsed command-line arguments (--tag plus the options above)."""
    return build_picks(getattr(args, 'tag', None), allow_empty=bool(getattr(args, 'near', None)),
                       **{name: getattr(args, name, None) for name in FILTER_OPTIONS})


def geocode(place, urlopen=urllib.request.urlopen):
    """Look up "City, State, Country" on OpenStreetMap. Returns (lat, lng), or None if not found.
    Raises ValueError if the service cannot be reached."""
    url = GEOCODER_URL + '?' + urllib.parse.urlencode({'q': place, 'format': 'json', 'limit': 1})
    request = urllib.request.Request(
        url, headers={'User-Agent': os.environ.get('GEOCODER_USER_AGENT') or GEOCODER_USER_AGENT})
    try:
        with urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode('utf-8'))
        if not data:
            return None
        return float(data[0]['lat']), float(data[0]['lon'])
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        raise ValueError('Could not reach the place lookup (OpenStreetMap). Check your internet '
                         'connection and try again in a minute.') from None


def near_from_args(args, geocode_fn=geocode):
    """Turn --near / --radius / --no-virtual into the travel-radius settings, or None if no --near."""
    near = (getattr(args, 'near', None) or '').strip()
    radius = getattr(args, 'radius', None)
    no_virtual = bool(getattr(args, 'no_virtual', False))
    if not near:
        if getattr(args, 'near', None) is not None:
            raise ValueError('--near needs a place, e.g. --near "Detroit, MI, USA"')
        if radius is not None or no_virtual:
            raise ValueError('--radius and --no-virtual need a place: add --near "City, State, Country"')
        return None
    radius = DEFAULT_RADIUS_MILES if radius is None else radius
    if not 1 <= radius <= MAX_RADIUS_MILES:
        raise ValueError(f'Use --radius 1-{MAX_RADIUS_MILES} (miles)')
    point = geocode_fn(near)
    if point is None:
        raise ValueError(f'Could not find "{near}". Try City, State, Country, e.g. --near "Detroit, MI, USA"')
    return {'lat': point[0], 'lng': point[1], 'radius': int(radius) if radius == int(radius) else radius,
            'label': near, 'include_virtual': not no_virtual}


def geocode_missing(db, geocode_fn=geocode, sleep=time.sleep, out=print):
    """Look up coordinates for every speaker whose current location has not been looked up yet and
    save them. Safe to run again: it only does people who are new or changed their location."""
    rows = db.rpc('locations_to_geocode', {}) or []
    if not rows:
        return 'Every speaker location is already looked up.'
    found, not_found = 0, []
    for i, row in enumerate(rows):
        if i:
            sleep(1.1)  # OpenStreetMap allows about one lookup per second
        point = geocode_fn(row['location_text'])
        db.rpc('save_location', {'p_response_id': row['response_id'], 'p_location_text': row['location_text'],
                                 'p_lat': point[0] if point else None, 'p_lng': point[1] if point else None})
        found += bool(point)
        if not point:
            not_found.append(row['location_text'])
        out(f"{i + 1}/{len(rows)} {row['location_text']}: {'found' if point else 'not found'}")
    summary = f'Looked up {found} of {len(rows)} locations.'
    if not_found:
        summary += (' Not found, so they will not count as near anywhere: ' + '; '.join(not_found)
                    + '. Fix the spelling on the form answer and run this again.')
    return summary
