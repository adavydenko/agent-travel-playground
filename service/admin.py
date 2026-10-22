"""Local-only operator commands; no admin HTTP endpoint."""

import argparse
import csv
import json
import os
import sys

from catalog import load_catalog
from store import DEFAULT_DB_PATH, TASK_KIND, TOKEN_TTL_HOURS, Store


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=os.environ.get('VERIFY_DB', DEFAULT_DB_PATH))
    parser.add_argument(
        '--config',
        help='JSON мероприятий; по умолчанию VERIFY_CONFIG или config/events.json',
    )
    commands = parser.add_subparsers(dest='command', required=True)

    new_token = commands.add_parser('new-token')
    new_token.add_argument('student')
    new_token.add_argument('--hours', type=float, default=TOKEN_TTL_HOURS)

    revoke = commands.add_parser('revoke')
    revoke.add_argument('student')

    reset = commands.add_parser('reset')
    reset.add_argument('student')
    reset.add_argument('kind', choices=[TASK_KIND])

    commands.add_parser('report')
    commands.add_parser('check-config')

    events = commands.add_parser('events')
    events.add_argument('student')

    args = parser.parse_args(argv)
    if args.command == 'check-config':
        _print_events(load_catalog(args.config))
        return

    db = Store(args.db, args.config)
    if args.command == 'new-token':
        print(db.issue(args.student, args.hours))
    elif args.command == 'revoke':
        db.revoke(args.student)
    elif args.command == 'reset':
        db.reset(args.student, args.kind)
    elif args.command == 'report':
        _write_report(db.report())
    elif args.command == 'events':
        for event in db.events(args.student):
            print(json.dumps(event, ensure_ascii=False))
    else:
        raise AssertionError(args.command)


def _print_events(catalog):
    for event in catalog['events']:
        span = f'{event["start_date"]} — {event["end_date"]}'
        print(f'{event["id"]}: {event["name"]} ({span})')


def _write_report(rows):
    writer = csv.DictWriter(
        sys.stdout,
        fieldnames=[
            'student',
            'kind',
            'event_id',
            'task_id',
            'receipt',
            'status',
            'calls',
        ],
    )
    writer.writeheader()
    # Prevent formula execution if an operator chose a spreadsheet-sensitive identifier.
    for row in rows:
        writer.writerow(
            {
                key: (
                    "'" + value
                    if isinstance(value, str) and value.startswith(('=', '+', '-', '@'))
                    else value
                )
                for key, value in row.items()
            }
        )


if __name__ == '__main__':
    main()
