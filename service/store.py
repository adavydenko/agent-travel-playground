"""Transactional state and evidence for the travel verification exercise."""
import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from travel import new_attempt, apply
from catalog import load_catalog


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Store:
    def __init__(self, path, config_path=None):
        self.path = str(path)
        self.world = load_catalog(config_path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS students (
                    student TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
                    expires REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, student TEXT NOT NULL, kind TEXT NOT NULL,
                    expires REAL NOT NULL, state TEXT NOT NULL,
                    receipt TEXT, UNIQUE(student, kind));
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
                    student TEXT NOT NULL, task TEXT NOT NULL,
                    tool TEXT NOT NULL, args TEXT NOT NULL, result TEXT NOT NULL);
            ''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(students)')}
            if 'event_id' not in columns:
                db.execute('ALTER TABLE students ADD COLUMN event_id TEXT')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def issue(self, student, hours=168):
        if not student or len(student) > 80 or not 0 < hours <= 720:
            raise ValueError('Use a pseudonymous student ID <=80 chars and 0<hours<=720')
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute(
                'INSERT INTO students(student,token_hash,expires,active,event_id) VALUES(?,?,?,1,NULL) '
                'ON CONFLICT(student) DO UPDATE SET token_hash=excluded.token_hash, '
                'expires=excluded.expires, active=1, event_id=NULL',
                (student, digest(token), time.time() + hours * 3600))
        return token

    def authenticate(self, token):
        with self.connect() as db:
            row = db.execute('SELECT student FROM students WHERE token_hash=? AND active=1 '
                             'AND expires>?', (digest(token), time.time())).fetchone()
        return row['student'] if row else None

    def revoke(self, student):
        with self.connect() as db:
            db.execute('UPDATE students SET active=0 WHERE student=?', (student,))

    def reset(self, student, kind):
        # Old audit events remain; new task IDs cannot be confused with old receipts.
        with self.connect() as db:
            db.execute('DELETE FROM tasks WHERE student=? AND kind=?', (student, kind))

    def call(self, student, tool, args):
        """One transaction includes state change and its audit event."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            enrolled = db.execute(
                'SELECT student FROM students WHERE student=? AND active=1 AND expires>?',
                (student, time.time())).fetchone()
            if not enrolled:
                return dict(ok=False, error='access_expired')
            row = db.execute('SELECT * FROM tasks WHERE student=? AND kind=?',
                             (student, 'travel')).fetchone()
            if row is None:
                task_id = secrets.token_hex(16)
                db.execute(
                    'INSERT INTO tasks(id,student,kind,expires,state,receipt) VALUES(?,?,?,?,?,NULL)',
                    (task_id, student, 'travel', time.time() + 7200,
                     json.dumps(new_attempt(self.world))))
                row = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            task_id = row['id']
            if row['expires'] <= time.time():
                result = dict(ok=False, error='task_expired')
            else:
                count = db.execute(
                    'SELECT COUNT(*) FROM events WHERE task=? AND student=?',
                    (task_id, student)).fetchone()[0]
                if count >= 100:
                    result = dict(ok=False, error='call_budget_exhausted')
                else:
                    state = json.loads(row['state'])
                    result = (apply(state, tool, args) if row['kind'] == 'travel'
                              else dict(ok=False, error='retired_task'))
                    db.execute('UPDATE tasks SET state=? WHERE id=?',
                               (json.dumps(state), task_id))
            db.execute(
                'INSERT INTO events(at,student,task,tool,args,result) VALUES(?,?,?,?,?,?)',
                (time.time(), student, task_id, tool, json.dumps(args), json.dumps(result)))
            return result

    def report(self):
        with self.connect() as db:
            return [dict(x) for x in db.execute('''
                SELECT s.student, k.kind, t.id AS task_id, t.receipt,
                s.event_id AS event_id,
                CASE WHEN t.id IS NULL THEN 'not_started'
                     WHEN t.expires<=? THEN 'expired' ELSE 'in_progress' END AS status,
                (SELECT COUNT(*) FROM events e WHERE e.task=t.id AND e.student=t.student) AS calls
                FROM students s CROSS JOIN (SELECT 'travel' kind) k
                LEFT JOIN tasks t ON s.student=t.student AND k.kind=t.kind
                ORDER BY s.student,k.kind''', (time.time(),))]

    def events(self, student):
        with self.connect() as db:
            return [dict(x) for x in db.execute('SELECT * FROM events WHERE student=? ORDER BY seq', (student,))]
