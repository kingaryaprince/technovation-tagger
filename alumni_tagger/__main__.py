import argparse
import getpass
import json
import os
from pathlib import Path
import sys
import time
from datetime import datetime, timezone
from .core import ROOT, PROMPT_VERSION, load_taxonomy, schema, tag_profile
from .scoring import compare, summarize, markdown
from .clients import OpenAIClient, SupabaseClient, settings, ServiceError
from .worker import run_once
from . import search as speaker_search


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def configure():
    path = ROOT / 'config.local.json'
    config = settings()
    print('Press Enter to keep an existing value. Keys are hidden while typing.')
    for name in ['OPENAI_API_KEY', 'OPENAI_MODEL', 'SUPABASE_URL', 'SUPABASE_KEY']:
        hint = 'set' if config.get(name) else 'empty'
        prompt = f'{name} [{hint}]: '
        value = getpass.getpass(prompt) if 'KEY' in name else input(prompt)
        if value.strip():
            config[name] = value.strip()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as out:
        json.dump(config, out, indent=2)
    os.chmod(path, 0o600)
    print('Saved config.local.json. Keep it private and out of shared ZIPs.')


NOTE = ('Expected tags come from the team\'s sample profiles (Questionnaire Brainstorming PDF). '
        'Interests/skills/story answers were written to paraphrase those tags, so this is a smoke test, '
        'not an independent accuracy benchmark. Human review of the misses is the point.')


def load_samples():
    return json.loads((ROOT / 'data/sample_profiles.json').read_text(encoding='utf-8'))


def evaluate(samples, api, model, out, runs=1):
    report = {'kind': 'live-model-evaluation', 'model': model, 'runs': runs, 'note': NOTE, 'profiles': []}
    for sample in samples:
        results = []
        for _ in range(runs):
            results.append(tag_profile(sample['answers'], model, api))
        report['profiles'].append({
            'id': sample['id'], 'provenance': sample['provenance'], 'expected': sample['expected'],
            'runs': [r['tags'] for r in results], 'evidence_first_run': results[0]['evidence'],
            'comparison_first_run': compare(sample['expected'], results[0]['tags'])})
        write_json(out, report)  # keep finished work if a later API call fails
        print(f"Evaluated {sample['id']} ({runs} run{'s' if runs > 1 else ''})")
    report['summary'] = summarize(report['profiles'])
    write_json(out, report)
    md = Path(out).with_suffix('.md')
    md.write_text(markdown(report['summary'], f'Tagger evaluation ({model}, prompt {PROMPT_VERSION})',
                           runs, len(samples), NOTE), encoding='utf-8')
    print('Saved', out, 'and', md)
    print(md.read_text(encoding='utf-8'))


def ingest_samples(db, samples):
    """Push the named sample profiles through the same RPC the Google Form uses."""
    now = datetime.now(timezone.utc).isoformat()
    loaded = 0
    for s in samples:
        if not s.get('email'):
            continue  # edge cases have no identity; they are for `evaluate` only
        # speaker_answers = the form's checkbox/select answers (availability, audiences, ...).
        # The tagger never sends them to OpenAI; the speaker search filters on them.
        answers = {**s['answers'], **s.get('speaker_answers', {}),
                   'full_name': s['full_name'], 'email': s['email'], '_sample_id': s['id']}
        db.rpc('ingest_alumni_intake', {'p_email': s['email'], 'p_full_name': s['full_name'],
               'p_answers': answers, 'p_response_id': 'sample-' + s['id'], 'p_submitted_at': now})
        loaded += 1
    return loaded


def sample_report(db, samples, out):
    rows = db.select('alumni_intake_responses?select=email,tagging_status,tags,prompt_version,model'
                     '&email=like.sample-*%40example.com') or []
    by_email = {r['email']: r for r in rows}
    profiles, not_done = [], []
    for s in samples:
        row = by_email.get(s.get('email'))
        if not row:
            continue
        if row['tagging_status'] != 'completed':
            not_done.append(f"{s['id']}: {row['tagging_status']}")
            continue
        profiles.append({'id': s['id'], 'expected': s['expected'], 'runs': [row['tags']]})
    if not profiles:
        raise ValueError('No completed sample rows yet. Run load-samples, then the worker.')
    summary = summarize(profiles)
    report = {'kind': 'supabase-sample-report', 'profiles': profiles, 'summary': summary,
              'not_completed': not_done}
    write_json(out, report)
    md = Path(out).with_suffix('.md')
    note = NOTE + (f"\n\nNot completed yet: {', '.join(not_done)}" if not_done else '')
    md.write_text(markdown(summary, 'Tags saved in Supabase vs expected', 1, len(profiles), note), encoding='utf-8')
    print(md.read_text(encoding='utf-8'))
    print('Saved', out, 'and', md)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Technovation alumni tagging, Python 3.10+, no pip dependencies')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('configure', help='Enter keys locally with hidden prompts')
    commands.add_parser('check-db', help='Check the Supabase table; does not call OpenAI')
    commands.add_parser('schema', help='Print the structured-output schema; no API calls')
    demo = commands.add_parser('demo', help='Tag one provided sample through OpenAI; no database writes')
    demo.add_argument('--sample', default='01-amina')
    demo.add_argument('--out', default='output/demo.json')
    tag = commands.add_parser('tag', help='Tag a JSON answers object through OpenAI; no database writes')
    tag.add_argument('file'); tag.add_argument('--out', default='output/tags.json')
    evaluate_p = commands.add_parser('evaluate', help='Paid live-model test on all sample profiles; no database writes')
    evaluate_p.add_argument('--limit', type=int, default=0, help='Only the first N samples (default: all)')
    evaluate_p.add_argument('--runs', type=int, default=1, help='Tag each profile N times to check consistency')
    evaluate_p.add_argument('--out', default='output/evaluation.json')
    commands.add_parser('load-samples', help='Insert the 20 sample profiles into Supabase as pending jobs')
    report_p = commands.add_parser('sample-report', help='Score the sample tags the worker saved in Supabase')
    report_p.add_argument('--out', default='output/supabase-sample-report.json')
    find = commands.add_parser('search', help='Speaker search: top matches for the picked tags; no OpenAI call')
    find.add_argument('--tag', action='append', metavar='CATEGORY=TAG',
                      help='Repeat for each tag, e.g. --tag audiences=Students --tag skills=Python')
    find.add_argument('--limit', type=int, default=5, help='How many people (default 5)')
    find.add_argument('--all', action='store_true', help='Include people who did not pick a speaking type')
    find.add_argument('--json', action='store_true', help='Print the full results as JSON')
    speaker_search.add_filter_arguments(find)
    commands.add_parser('geocode-locations', help="Look up coordinates for each speaker's current location (needed for --near)")
    options = commands.add_parser('filter-options', help='List every tag people have, with head counts')
    options.add_argument('--category', help='Only this category, e.g. audiences')
    work = commands.add_parser('worker', help='Read pending Supabase jobs, call OpenAI, save tags')
    work.add_argument('--limit', type=int, default=5, help='Max jobs per pass')
    work.add_argument('--watch', action='store_true', help='Keep polling for new form responses until Ctrl+C')
    work.add_argument('--interval', type=int, default=15)
    args = parser.parse_args(argv)
    try:
        if args.command == 'configure':
            configure(); return 0
        if args.command == 'schema':
            print(json.dumps(schema(load_taxonomy()), indent=2)); return 0
        config = settings()
        if args.command == 'check-db':
            SupabaseClient(config).check(); print('Supabase intake table is reachable.'); return 0
        if args.command == 'load-samples':
            n = ingest_samples(SupabaseClient(config), load_samples())
            print(f'Loaded {n} sample profiles as pending. Now run: python3 -m alumni_tagger worker --limit 25')
            return 0
        if args.command == 'sample-report':
            sample_report(SupabaseClient(config), load_samples(), args.out); return 0
        if args.command == 'search':
            picks = speaker_search.picks_from_args(args)
            near = speaker_search.near_from_args(args)
            rows = speaker_search.search(SupabaseClient(config), picks, args.limit, not args.all,
                                         strict=args.strict)
            print(json.dumps(rows, ensure_ascii=False, indent=2) if args.json
                  else speaker_search.format_results(rows, strict=args.strict))
            return 0
        if args.command == 'geocode-locations':
            print(speaker_search.geocode_missing(SupabaseClient(config))); return 0
        if args.command == 'filter-options':
            rows = SupabaseClient(config).rpc('speaker_filter_options', {})
            print(speaker_search.format_options(rows, args.category)); return 0
        api = OpenAIClient(config)
        model = config['OPENAI_MODEL']
        if args.command in {'demo', 'evaluate'}:
            samples = load_samples()
            if args.command == 'demo':
                sample = next((x for x in samples if x['id'] == args.sample), None)
                if sample is None:
                    raise ValueError('Unknown sample ID; see data/sample_profiles.json')
                result = tag_profile(sample['answers'], model, api)
                write_json(args.out, result)
                print(json.dumps(result['tags'], ensure_ascii=False, indent=2)); print('Saved', args.out)
            else:
                if not 0 <= args.limit <= len(samples) or not 1 <= args.runs <= 5:
                    raise ValueError(f'Use --limit 0-{len(samples)} and --runs 1-5')
                evaluate(samples[:args.limit or len(samples)], api, model, args.out, args.runs)
        elif args.command == 'tag':
            value = json.loads(Path(args.file).read_text(encoding='utf-8'))
            result = tag_profile(value.get('answers', value), model, api)
            write_json(args.out, result); print('Saved', args.out)
        elif args.command == 'worker':
            if not 1 <= args.limit <= 100 or args.interval < 5:
                raise ValueError('Use --limit 1-100 and --interval >= 5')
            db = SupabaseClient(config)
            if not args.watch:
                print(json.dumps(run_once(db, api, model, args.limit)))
            else:
                print(f'Watching for new form responses every {args.interval}s. Press Ctrl+C to stop.')
                while True:
                    try:
                        counts = run_once(db, api, model, args.limit)
                        if sum(counts.values()):
                            print(json.dumps(counts))
                            continue  # more may be waiting; check again right away
                    except ServiceError as exc:
                        if exc.status in {400, 401, 403, 404}:
                            raise  # configuration problem: stop instead of retrying forever
                        print(f'{exc} Retrying in {args.interval}s.')
                    time.sleep(args.interval)
        return 0
    except (ValueError, OSError, ServiceError) as exc:
        # These exception messages are controlled, except local OS errors which contain paths only.
        if isinstance(exc, json.JSONDecodeError):
            print('Invalid local JSON file. Check its formatting.', file=sys.stderr)
        else:
            print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nStopped. Any claimed job can be reclaimed after its ten-minute lease expires.')
        return 130


if __name__ == '__main__':
    sys.exit(main())
