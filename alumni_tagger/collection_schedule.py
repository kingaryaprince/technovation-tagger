"""Alternate Monday collection, anchored to November 16, 2026 (UTC)."""
from datetime import date, datetime, timezone
import os

ANCHOR = date(2026, 11, 16)


def due(today):
    elapsed = (today - ANCHOR).days
    return elapsed >= 0 and elapsed % 14 == 0


if __name__ == '__main__':
    enabled = os.environ.get('GITHUB_EVENT_NAME') != 'schedule' or due(datetime.now(timezone.utc).date())
    output = 'enabled=' + str(enabled).lower() + '\n'
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
            stream.write(output)
    else:
        print(output, end='')
