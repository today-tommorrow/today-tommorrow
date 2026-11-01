"""Serialize physical worker calls even after a cancelled job releases its lease."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

# Different from (741201, 1), the short claim/heartbeat transaction lock.
EXECUTION_LOCK = (741201, 2)


@contextmanager
def execution_slot(factory: Callable[[], Session]) -> Iterator[bool]:
    with factory() as session:
        bind = session.get_bind()
        engine = bind.engine if isinstance(bind, Connection) else bind
    # A dedicated connection holds a session lock, not a transaction or ORM state.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        acquired = bool(
            connection.scalar(
                text("SELECT pg_try_advisory_lock(:namespace, :slot)"),
                {"namespace": EXECUTION_LOCK[0], "slot": EXECUTION_LOCK[1]},
            )
        )
        connection.commit()
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    connection.execute(
                        text("SELECT pg_advisory_unlock(:namespace, :slot)"),
                        {"namespace": EXECUTION_LOCK[0], "slot": EXECUTION_LOCK[1]},
                    )
                    connection.commit()
                except SQLAlchemyError:
                    # Never return a possibly locked session to the pool.
                    connection.invalidate()
