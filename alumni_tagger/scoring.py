"""Score tagger output against expected sample tags, and measure run-to-run consistency.

Pure functions, no API calls. Used by `evaluate` (live model, no database) and
`sample-report` (tags the worker already saved in Supabase).
"""
from collections import Counter


def compare(expected, actual):
    """expected/actual: {category: [tags]}. Only categories present in expected are scored."""
    out = {}
    for category, exp in expected.items():
        exp, act = set(exp), set(actual.get(category) or [])
        out[category] = {'tp': sorted(exp & act), 'missing': sorted(exp - act), 'extra': sorted(act - exp)}
    return out


def consistency(runs):
    """runs: list of {category: [tags]} for one profile. Returns categories whose tags changed."""
    if len(runs) < 2:
        return {}
    changed = {}
    for category in runs[0]:
        variants = {tuple(sorted(r.get(category) or [])) for r in runs}
        if len(variants) > 1:
            changed[category] = [list(v) for v in sorted(variants)]
    return changed


def summarize(profiles):
    """profiles: [{'id', 'expected', 'runs': [tags, ...]}]. Scores every run."""
    per_cat = {}
    missing, extra = Counter(), Counter()
    unstable, stable_cats, total_cats = [], 0, 0
    for p in profiles:
        for run in p['runs']:
            for category, c in compare(p['expected'], run).items():
                s = per_cat.setdefault(category, {'tp': 0, 'fp': 0, 'fn': 0})
                s['tp'] += len(c['tp']); s['fp'] += len(c['extra']); s['fn'] += len(c['missing'])
                missing.update(f'{category}: {t}' for t in c['missing'])
                extra.update(f'{category}: {t}' for t in c['extra'])
        if len(p['runs']) > 1:
            changed = consistency(p['runs'])
            total_cats += len(p['runs'][0]); stable_cats += len(p['runs'][0]) - len(changed)
            if changed:
                unstable.append({'id': p['id'], 'changed': changed})

    def pr(s):
        p = s['tp'] / (s['tp'] + s['fp']) if s['tp'] + s['fp'] else None
        r = s['tp'] / (s['tp'] + s['fn']) if s['tp'] + s['fn'] else None
        return {**s, 'precision': p, 'recall': r}

    totals = {'tp': 0, 'fp': 0, 'fn': 0}
    for s in per_cat.values():
        for k in totals:
            totals[k] += s[k]
    return {
        'per_category': {k: pr(v) for k, v in per_cat.items()},
        'overall': pr(totals),
        'most_missed': missing.most_common(15),
        'most_extra': extra.most_common(15),
        'consistency': ({'identical_category_share': stable_cats / total_cats, 'unstable_profiles': unstable}
                        if total_cats else None),
    }


def _pct(x):
    return '-' if x is None else f'{x:.0%}'


def markdown(summary, title, runs, n_profiles, note=''):
    lines = [f'# {title}', '', f'{n_profiles} profiles, {runs} run(s) each.', '']
    if note:
        lines += [note, '']
    o = summary['overall']
    lines += [f"Overall: precision {_pct(o['precision'])}, recall {_pct(o['recall'])} "
              f"({o['tp']} correct, {o['fp']} extra, {o['fn']} missing).", '',
              '| Category | Precision | Recall | Correct | Extra | Missing |', '|---|---|---|---|---|---|']
    for cat, s in summary['per_category'].items():
        lines.append(f"| {cat} | {_pct(s['precision'])} | {_pct(s['recall'])} | {s['tp']} | {s['fp']} | {s['fn']} |")
    lines += ['', '## Most often missed', '']
    lines += [f'- {t} ({n}x)' for t, n in summary['most_missed']] or ['- none']
    lines += ['', '## Most often added but not expected', '']
    lines += [f'- {t} ({n}x)' for t, n in summary['most_extra']] or ['- none']
    c = summary['consistency']
    if c:
        lines += ['', '## Consistency across runs', '',
                  f"{_pct(c['identical_category_share'])} of (profile, category) pairs got identical tags every run.", '']
        for u in c['unstable_profiles']:
            for cat, variants in u['changed'].items():
                lines.append(f"- {u['id']} / {cat}: " + ' vs '.join('[' + ', '.join(v) + ']' for v in variants))
        if not c['unstable_profiles']:
            lines.append('- Every profile got the same tags on every run.')
    return '\n'.join(lines) + '\n'
