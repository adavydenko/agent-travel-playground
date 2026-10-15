"""Local-only operator commands; no admin HTTP endpoint."""
import argparse
import csv
import json
import os
import sys
from store import Store
from catalog import load_catalog

p = argparse.ArgumentParser()
p.add_argument('--db', default=os.environ.get('VERIFY_DB', 'data/verification.sqlite3'))
p.add_argument('--config', help='JSON мероприятий; по умолчанию VERIFY_CONFIG или config/events.json')
sub = p.add_subparsers(dest='command', required=True)
s = sub.add_parser('issue'); s.add_argument('student'); s.add_argument('--hours', type=float, default=168)
s.add_argument('--event', help='ID мероприятия; при перевыпуске токена назначение сохраняется')
s = sub.add_parser('revoke'); s.add_argument('student')
s = sub.add_parser('reset'); s.add_argument('student'); s.add_argument('kind', choices=['travel'])
sub.add_parser('report')
sub.add_parser('check-config')
s = sub.add_parser('events'); s.add_argument('student')
a = p.parse_args()
if a.command == 'check-config':
    for event in load_catalog(a.config).values():
        print(f"{event['id']}: {event['name']} ({event['start_date']} — {event['end_date']})")
    sys.exit(0)
db = Store(a.db, a.config)
if a.command == 'issue':
    print(db.issue(a.student, a.hours, a.event))
elif a.command == 'revoke':
    db.revoke(a.student)
elif a.command == 'reset':
    db.reset(a.student, a.kind)
elif a.command == 'report':
    rows = db.report()
    writer = csv.DictWriter(sys.stdout, fieldnames=['student','kind','event_id','task_id','receipt','status','calls'])
    writer.writeheader()
    # Prevent formula execution if an operator chose a spreadsheet-sensitive identifier.
    for row in rows:
        writer.writerow({k: "'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v
                         for k,v in row.items()})
else:
    for event in db.events(a.student):
        print(json.dumps(event, ensure_ascii=False))
