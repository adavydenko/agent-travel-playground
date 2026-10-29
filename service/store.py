"""Access, attempts and audit evidence persisted through SQLAlchemy sessions."""

import hashlib
import json
import logging
import secrets
import time
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from catalog import load_catalog
from contracts import State, ToolResponse, ToolResult
from db.database import Database
from db.models import Access, Attempt, AuditEvent
DEFAULT_DB_PATH = 'data/verification.sqlite3'
TASK_KIND = 'travel'
TOKEN_TTL_HOURS = 168
MAX_TOKEN_HOURS = 720
MAX_STUDENT_LEN = 80
ATTEMPT_TTL_SECONDS = 2 * 60 * 60
CALL_BUDGET = 100

Result = dict[str, Any]
Body = Callable[[State], ToolResult]
logger = logging.getLogger(__name__)


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _call_count(session: Session, attempt: Attempt) -> int:
    return (
        session.scalar(
            select(func.count(AuditEvent.seq)).where(
                AuditEvent.task == attempt.id, AuditEvent.student == attempt.student
            )
        )
        or 0
    )


def _attempt_status(attempt: Attempt | None, now: float) -> str:
    if attempt is None:
        return 'not_started'
    return 'expired' if attempt.expires <= now else 'in_progress'


class Store:
    def __init__(self, path: str | Path, config_path: str | Path | None = None) -> None:
        self.path = str(path)
        self.config_path = config_path
        self.database = Database(path)

    def close(self) -> None:
        self.database.close()

    def issue(self, student: str, hours: float = TOKEN_TTL_HOURS) -> str:
        if (
            not student
            or len(student) > MAX_STUDENT_LEN
            or not 0 < hours <= MAX_TOKEN_HOURS
        ):
            raise ValueError(
                f'Use a pseudonymous student ID <={MAX_STUDENT_LEN} chars '
                f'and 0<hours<={MAX_TOKEN_HOURS}'
            )
        token = secrets.token_urlsafe(32)
        with self.database.write_sessions.begin() as session:
            access = session.get(Access, student)
            if access is None:
                access = Access(student=student)
                session.add(access)
            access.token_hash = digest(token)
            access.expires = time.time() + hours * 3600
            access.active = True
            access.event_id = None
        logger.info('access_issued student=%r', student)
        return token

    def authenticate(self, token: str) -> str | None:
        with self.database.sessions() as session:
            return session.scalar(
                select(Access.student).where(
                    Access.token_hash == digest(token),
                    Access.active.is_(True),
                    Access.expires > time.time(),
                )
            )

    def revoke(self, student: str) -> None:
        with self.database.write_sessions.begin() as session:
            access = session.get(Access, student)
            if access is not None:
                access.active = False
        logger.info('access_revoked student=%r', student)

    def reset(self, student: str, kind: str) -> None:
        with self.database.write_sessions.begin() as session:
            attempt = session.scalar(
                select(Attempt).where(Attempt.student == student, Attempt.kind == kind)
            )
            if attempt is not None:
                session.delete(attempt)
        logger.info('attempt_reset student=%r kind=%s', student, kind)

    def clear_bookings(self) -> int:
        """Clear all travel bookings atomically; keep access, attempts and audit."""
        cleared = 0
        with self.database.write_sessions.begin() as session:
            attempts = session.scalars(select(Attempt).where(Attempt.kind == TASK_KIND))
            for attempt in attempts:
                state = attempt.state
                flights = state.get('flight_bookings') or []
                hotels = state.get('hotel_bookings') or []
                count = len(flights) + len(hotels)
                if count:
                    cleared += count
                    attempt.state = {
                        **state,
                        'flight_bookings': [],
                        'hotel_bookings': [],
                    }
        logger.info('bookings_cleared count=%d', cleared)
        return cleared

    def call(
        self, student: str, operation: Callable[..., ToolResult], args: Result | None = None
    ) -> ToolResult:
        """Run operation(state, **args); kept for tests and direct Store clients."""
        recorded = dict(args or {})
        return self.execute(
            student,
            operation.__name__,
            lambda state: operation(state, **recorded),
            recorded,
        )

    def execute(
        self,
        student: str,
        tool: str,
        body: Body,
        args: Result | None = None,
    ) -> ToolResult:
        """Run body(state) inside one attempt transaction and audit the outcome."""
        recorded_args = dict(args or {})
        with self.database.write_sessions.begin() as session:
            now = time.time()
            access = session.get(Access, student)
            if access is None or not access.active or access.expires <= now:
                return {'ok': False, 'error': 'access_expired'}
            attempt = self._get_or_create_attempt(session, student, now)
            if attempt.expires <= now:
                result: ToolResult = {'ok': False, 'error': 'task_expired'}
            elif _call_count(session, attempt) >= CALL_BUDGET:
                result = {'ok': False, 'error': 'call_budget_exhausted'}
            else:
                result = self._run_body(attempt, body)
            result = ToolResponse.model_validate(result).root
            session.add(
                AuditEvent(
                    at=now,
                    student=student,
                    task=attempt.id,
                    tool=tool,
                    args=json.dumps(recorded_args),
                    result=json.dumps(result),
                )
            )
        logger.info(
            'operation_completed student=%r attempt=%s tool=%s ok=%s error=%s',
            student,
            attempt.id,
            tool,
            result['ok'],
            result.get('error'),
        )
        return result

    def _run_body(self, attempt: Attempt, body: Body) -> ToolResult:
        try:
            world = load_catalog(self.config_path)
        except (OSError, ValueError):
            logger.warning('catalog_unavailable')
            return {
                'ok': False,
                'error': 'invalid_config',
                'message': (
                    'Каталог недоступен или некорректен. Обратитесь к оператору.'
                ),
            }
        # Copy nested values so assigning state back is visible to ORM tracking.
        saved = deepcopy(attempt.state)
        state = State(
            **world,
            flight_bookings=list(saved.get('flight_bookings') or []),
            hotel_bookings=list(saved.get('hotel_bookings') or []),
            registrations=saved['registrations'],
        )
        result = body(state)
        # Legacy attempts can contain a catalog; preserve all other saved fields.
        attempt.state = {
            **{key: value for key, value in saved.items() if key not in world},
            'flight_bookings': state['flight_bookings'],
            'hotel_bookings': state['hotel_bookings'],
            'registrations': state['registrations'],
        }
        return result

    @staticmethod
    def _get_or_create_attempt(session: Session, student: str, now: float) -> Attempt:
        attempt = session.scalar(
            select(Attempt).where(Attempt.student == student, Attempt.kind == TASK_KIND)
        )
        if attempt is None:
            attempt = Attempt(
                id=secrets.token_hex(16),
                student=student,
                kind=TASK_KIND,
                expires=now + ATTEMPT_TTL_SECONDS,
                state=dict(
                    flight_bookings=[], hotel_bookings=[], registrations=[]
                ),
            )
            session.add(attempt)
            session.flush()
        return attempt

    def report(self) -> list[Result]:
        counts = (
            select(AuditEvent.task, AuditEvent.student, func.count().label('calls'))
            .group_by(AuditEvent.task, AuditEvent.student)
            .subquery()
        )
        statement = (
            select(Access, Attempt, func.coalesce(counts.c.calls, 0))
            .outerjoin(
                Attempt,
                (Attempt.student == Access.student) & (Attempt.kind == TASK_KIND),
            )
            .outerjoin(
                counts,
                (counts.c.task == Attempt.id) & (counts.c.student == Access.student),
            )
            .order_by(Access.student)
        )
        now = time.time()
        with self.database.sessions() as session:
            return [
                dict(
                    student=access.student,
                    kind=TASK_KIND,
                    event_id=access.event_id,
                    task_id=attempt.id if attempt else None,
                    receipt=attempt.receipt if attempt else None,
                    status=_attempt_status(attempt, now),
                    calls=calls,
                )
                for access, attempt, calls in session.execute(statement)
            ]

    def events(self, student: str) -> list[Result]:
        statement = (
            select(AuditEvent)
            .where(AuditEvent.student == student)
            .order_by(AuditEvent.seq)
        )
        with self.database.sessions() as session:
            return [
                dict(
                    seq=entry.seq,
                    at=entry.at,
                    student=entry.student,
                    task=entry.task,
                    tool=entry.tool,
                    args=entry.args,
                    result=entry.result,
                )
                for entry in session.scalars(statement)
            ]
