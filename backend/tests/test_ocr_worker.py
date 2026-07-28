import asyncio
import os
import signal
import subprocess
import sys

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app import ocr_worker


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _RecordingConnection:
    def __init__(self, acquire_result=True):
        self.acquire_result = acquire_result
        self.calls = []
        self.commit_count = 0

    async def execute(self, statement, parameters=None):
        sql = str(statement)
        self.calls.append((sql, parameters))
        if "pg_try_advisory_lock" in sql:
            return _ScalarResult(self.acquire_result)
        return _ScalarResult(True)

    async def commit(self):
        self.commit_count += 1


@pytest.mark.skipif(os.name != "posix", reason="worker runs in a Linux container")
def test_sigill_exit_handler_terminates_pid_one_style_process():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os, signal; "
                "from app.ocr_worker import install_sigill_exit_handler; "
                "install_sigill_exit_handler(); "
                "os.kill(os.getpid(), signal.SIGILL)"
            ),
        ],
        check=False,
    )

    assert result.returncode == signal.SIGILL


@pytest.mark.asyncio
async def test_advisory_lock_uses_postgres_lock_and_unlock_sql():
    connection = _RecordingConnection(acquire_result=True)

    assert await ocr_worker.try_acquire_worker_lock(connection) is True
    await ocr_worker.release_worker_lock(connection)

    assert "pg_try_advisory_lock" in connection.calls[0][0]
    assert connection.calls[0][1] == {
        "lock_key": ocr_worker.WORKER_ADVISORY_LOCK_KEY
    }
    assert "pg_advisory_unlock" in connection.calls[1][0]
    assert connection.calls[1][1] == {
        "lock_key": ocr_worker.WORKER_ADVISORY_LOCK_KEY
    }
    assert connection.commit_count == 2

    contended_connection = _RecordingConnection(acquire_result=False)
    assert await ocr_worker.try_acquire_worker_lock(contended_connection) is False


@pytest.mark.asyncio
async def test_worker_lock_session_verification_detects_connection_replacement():
    connection = _RecordingConnection()

    assert await ocr_worker.worker_lock_session_id(connection) == 1
    await ocr_worker.verify_worker_lock_session(connection, 1)
    with pytest.raises(RuntimeError, match="database session changed"):
        await ocr_worker.verify_worker_lock_session(connection, 2)


@pytest.mark.asyncio
async def test_worker_initializes_locks_recovers_and_prewarms_before_claiming(
    monkeypatch,
):
    events = []
    stop_event = asyncio.Event()
    lock_connection = object()

    class _ConnectionContext:
        async def __aenter__(self):
            return lock_connection

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    class _FakeEngine:
        def connect(self):
            return _ConnectionContext()

    async def initialize_database():
        events.append("initialize")

    async def wait_for_lock(connection, event):
        assert connection is lock_connection
        assert event is stop_event
        events.append("lock")
        return True

    async def recover_interrupted_batches():
        events.append("recover")
        return 0

    async def worker_lock_session_id(connection):
        assert connection is lock_connection
        events.append("session")
        return 123

    async def verify_worker_lock_session(connection, expected_backend_pid):
        assert connection is lock_connection
        assert expected_backend_pid == 123
        events.append("verify")

    def prewarm_ocr():
        events.append("prewarm")

    def install_sigill_exit_handler():
        events.append("sigill-exit")

    async def process_next_queued_document():
        events.append("claim")
        stop_event.set()
        return True

    async def release_worker_lock(connection):
        assert connection is lock_connection
        events.append("release")

    monkeypatch.setattr(ocr_worker, "engine", _FakeEngine())
    monkeypatch.setattr(ocr_worker, "initialize_database", initialize_database)
    monkeypatch.setattr(ocr_worker, "_wait_for_worker_lock", wait_for_lock)
    monkeypatch.setattr(
        ocr_worker,
        "recover_interrupted_batches",
        recover_interrupted_batches,
    )
    monkeypatch.setattr(
        ocr_worker,
        "worker_lock_session_id",
        worker_lock_session_id,
    )
    monkeypatch.setattr(
        ocr_worker,
        "verify_worker_lock_session",
        verify_worker_lock_session,
    )
    monkeypatch.setattr(ocr_worker, "prewarm_ocr", prewarm_ocr)
    monkeypatch.setattr(
        ocr_worker,
        "install_sigill_exit_handler",
        install_sigill_exit_handler,
    )
    monkeypatch.setattr(
        ocr_worker,
        "process_next_queued_document",
        process_next_queued_document,
    )
    monkeypatch.setattr(ocr_worker, "release_worker_lock", release_worker_lock)

    await ocr_worker.run_worker(stop_event)

    assert events == [
        "initialize",
        "lock",
        "session",
        "recover",
        "prewarm",
        "sigill-exit",
        "verify",
        "claim",
        "release",
    ]


@pytest.mark.postgres_integration
@pytest.mark.asyncio
async def test_postgres_advisory_lock_allows_only_one_worker(monkeypatch):
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL 未配置")

    engine = create_async_engine(database_url)
    test_lock_key = 0x5445535400000000 + (os.getpid() & 0xFFFFFFFF)
    monkeypatch.setattr(ocr_worker, "WORKER_ADVISORY_LOCK_KEY", test_lock_key)
    try:
        async with engine.connect() as first, engine.connect() as second:
            first_acquired = False
            second_acquired = False
            try:
                first_acquired = await ocr_worker.try_acquire_worker_lock(first)
                assert first_acquired is True
                assert await ocr_worker.try_acquire_worker_lock(second) is False

                await ocr_worker.release_worker_lock(first)
                first_acquired = False
                second_acquired = await ocr_worker.try_acquire_worker_lock(second)
                assert second_acquired is True
                await ocr_worker.release_worker_lock(second)
                second_acquired = False
            finally:
                if first_acquired:
                    await ocr_worker.release_worker_lock(first)
                if second_acquired:
                    await ocr_worker.release_worker_lock(second)
    finally:
        await engine.dispose()
