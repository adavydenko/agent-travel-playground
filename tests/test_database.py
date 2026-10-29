"""Independent legacy SQLite fixtures and transaction failure checks."""

import hashlib
import json
import sqlite3
import time
from contextlib import closing

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import travel
from db.models import Attempt
from store import Store


@pytest.mark.parametrize('has_event_id', [False, True])
def test_existing_sqlite_database_is_preserved(tmp_path, has_event_id):
    path = tmp_path / 'legacy.sqlite3'
    token = 'test-only-legacy-token'
    state = {
        'flight_bookings': [
            {
                'id': 'old-booking',
                'status': 'active',
                'total_rub': 321,
                'flight_id': 'legacy-flight',
                'origin': 'SVO',
                'destination': 'VVO',
                'depart_date': '2027-03-10',
                'arrive_date': '2027-03-10',
            }
        ],
        'hotel_bookings': [],
        'registrations': [{'id': 'old-registration', 'status': 'active'}],
        'legacy_field': {'preserved': True},
    }
    # Deliberately create the old schema without using the new ORM mappings.
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript("""
            CREATE TABLE students (
                student TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
                expires REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY, student TEXT NOT NULL, kind TEXT NOT NULL,
                expires REAL NOT NULL, state TEXT NOT NULL, receipt TEXT,
                UNIQUE(student, kind)
            );
            CREATE TABLE events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
                student TEXT NOT NULL, task TEXT NOT NULL, tool TEXT NOT NULL,
                args TEXT NOT NULL, result TEXT NOT NULL
            );
        """)
        if has_event_id:
            connection.execute('ALTER TABLE students ADD COLUMN event_id TEXT')
        connection.execute(
            'INSERT INTO students (student, token_hash, expires) VALUES (?, ?, ?)',
            ('legacy', hashlib.sha256(token.encode()).hexdigest(), time.time() + 3600),
        )
        connection.execute(
            'INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?)',
            (
                'old-attempt',
                'legacy',
                'travel',
                time.time() + 3600,
                json.dumps(state),
                '{"saved":true}',
            ),
        )
        connection.execute(
            'INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)',
            (7, 123.0, 'legacy', 'old-attempt', 'legacy_tool', '{}', '{"saved":true}'),
        )
    with closing(Store(path)) as store:
        assert store.authenticate(token) == 'legacy'
        before = store.events('legacy')
        assert before[0]['result'] == '{"saved":true}'
        result = store.call('legacy', travel.list_flight_bookings, {})
        assert result['total_rub'] == 321
        assert result['flight_bookings'][0]['id'] == 'old-booking'
        report = store.report()[0]
        assert report['task_id'] == 'old-attempt'
        assert report['receipt'] == '{"saved":true}'
        assert report['calls'] == 2
        assert store.events('legacy')[:-1] == before
        assert store.events('legacy')[-1]['seq'] == 8
        rotated = store.issue('legacy')
        assert store.authenticate(token) is None
        assert store.authenticate(rotated) == 'legacy'
        assert store.report()[0]['task_id'] == 'old-attempt'
        with closing(sqlite3.connect(path)) as connection:
            queries = [
                (
                    'SELECT COUNT(*) FROM events WHERE task=? AND student=?',
                    ('old-attempt', 'legacy'),
                ),
                ('SELECT * FROM events WHERE student=? ORDER BY seq', ('legacy',)),
            ]
            for query, parameters in queries:
                plan = connection.execute('EXPLAIN QUERY PLAN ' + query, parameters)
                assert any('SEARCH events USING' in row[3] for row in plan)


def test_audit_write_failure_rolls_back_booking(tmp_path):
    path = tmp_path / 'atomic.sqlite3'
    with closing(Store(path)) as store:
        store.issue('alice')
        store.call('alice', travel.list_flight_bookings, {})
        before = store.events('alice')
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.executescript("""
                CREATE TRIGGER reject_event BEFORE INSERT ON events
                BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END;
            """)
        with pytest.raises(IntegrityError):
            store.call('alice', travel.book_flight, {'flight_id': 'vvo-out-night'})
        assert store.events('alice') == before
        with closing(sqlite3.connect(path)) as connection, connection:
            saved = json.loads(
                connection.execute('SELECT state FROM tasks').fetchone()[0]
            )
        assert saved.get('flight_bookings') == []
        assert saved.get('hotel_bookings') == []


def test_clear_bookings_failure_rolls_back_all_owners(tmp_path):
    path = tmp_path / 'atomic-clear.sqlite3'
    with closing(Store(path)) as store:
        for student in ('alice', 'bob'):
            store.issue(student)
            store.call(student, travel.book_flight, {'flight_id': 'vvo-out-night'})
        with store.database.sessions() as session:
            before = {
                attempt.id: attempt.state
                for attempt in session.scalars(select(Attempt))
            }
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.executescript("""
                CREATE TRIGGER reject_clear BEFORE UPDATE ON tasks
                WHEN OLD.student = 'bob'
                BEGIN SELECT RAISE(ABORT, 'clear unavailable'); END;
            """)
        with pytest.raises(IntegrityError):
            store.clear_bookings()
        with store.database.sessions() as session:
            after = {
                attempt.id: attempt.state
                for attempt in session.scalars(select(Attempt))
            }
        assert after == before
