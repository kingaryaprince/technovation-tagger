"""One-at-a-time leased jobs. SQL rejects writes from superseded submissions."""
from .core import tag_profile, ValidationError
from .clients import ServiceError


def run_once(db, api, model, limit=5, emit=print):
    counts = {'completed': 0, 'failed': 0, 'stale': 0}
    for _ in range(limit):
        rows = db.rpc('claim_alumni_tagging', {})
        if not rows:
            break
        job = rows[0]
        try:
            result = tag_profile(job['raw_answers'], model, api)
        except (ValidationError, ServiceError) as exc:
            # Store an error class only. Avoid logging free text, identities, keys, or API bodies.
            db.rpc('fail_alumni_tagging', {'p_id': job['id'], 'p_revision': job['revision'],
                   'p_token': job['lease_token'], 'p_error': type(exc).__name__})
            counts['failed'] += 1
            emit(f"Job {job['id']}: {type(exc).__name__}; check configuration or review the input.")
            if isinstance(exc, ServiceError):
                raise
            continue
        saved = db.rpc('complete_alumni_tagging', {
            'p_id': job['id'], 'p_revision': job['revision'],
            'p_token': job['lease_token'], 'p_result': result})
        counts['completed' if saved else 'stale'] += 1
        emit(f"Job {job['id']}: {'completed' if saved else 'superseded; discarded result'}")
    return counts
