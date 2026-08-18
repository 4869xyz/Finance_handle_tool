import asyncio
import logging
import signal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import reload_llm_from_env_file
from app.database import Base, engine, ensure_source_document_display_columns, ensure_processing_batch_package_id
from app.services.batch_service import (
    process_next_queued_document,
    recover_interrupted_batches,
)


logger = logging.getLogger(__name__)

WORKER_ADVISORY_LOCK_KEY = 0x494E564F4352574B  # "INVOCRWK"
POLL_INTERVAL_SECONDS = 1.0
LOCK_RETRY_SECONDS = 5.0


async def initialize_database() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await ensure_source_document_display_columns(connection)
        await ensure_processing_batch_package_id(connection)


async def try_acquire_worker_lock(connection: AsyncConnection) -> bool:
    result = await connection.execute(
        text("SELECT pg_try_advisory_lock(:lock_key)"),
        {"lock_key": WORKER_ADVISORY_LOCK_KEY},
    )
    acquired = bool(result.scalar_one())
    await connection.commit()
    return acquired


async def release_worker_lock(connection: AsyncConnection) -> None:
    await connection.execute(
        text("SELECT pg_advisory_unlock(:lock_key)"),
        {"lock_key": WORKER_ADVISORY_LOCK_KEY},
    )
    await connection.commit()


async def worker_lock_session_id(connection: AsyncConnection) -> int:
    result = await connection.execute(text("SELECT pg_backend_pid()"))
    backend_pid = int(result.scalar_one())
    await connection.commit()
    return backend_pid


async def verify_worker_lock_session(
    connection: AsyncConnection,
    expected_backend_pid: int,
) -> None:
    current_backend_pid = await worker_lock_session_id(connection)
    if current_backend_pid != expected_backend_pid:
        raise RuntimeError("OCR worker advisory-lock database session changed")


def prewarm_ocr() -> None:
    import numpy as np

    from app.services.ocr_service import get_ocr_service

    ocr = get_ocr_service().ocr
    warmup_image = np.full((64, 256, 3), 255, dtype=np.uint8)
    warmup_image[20:44, 20:236] = 0
    ocr.ocr(warmup_image, cls=True)


def install_sigill_exit_handler() -> None:
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    libc.signal.argtypes = [ctypes.c_int, ctypes.c_void_p]
    libc.signal.restype = ctypes.c_void_p
    exit_handler = ctypes.cast(libc._exit, ctypes.c_void_p)
    previous_handler = libc.signal(signal.SIGILL, exit_handler)
    if previous_handler == ctypes.c_void_p(-1).value:
        error_number = ctypes.get_errno()
        raise OSError(error_number, "Failed to install SIGILL exit handler")


async def _wait_or_stop(stop_event: asyncio.Event, timeout: float) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=timeout)
    except TimeoutError:
        pass


async def _wait_for_worker_lock(
    connection: AsyncConnection,
    stop_event: asyncio.Event,
) -> bool:
    while not stop_event.is_set():
        if await try_acquire_worker_lock(connection):
            return True
        logger.warning("Another OCR worker holds the advisory lock; retrying")
        await _wait_or_stop(stop_event, LOCK_RETRY_SECONDS)
    return False


async def run_worker(stop_event: asyncio.Event) -> None:
    await initialize_database()
    async with engine.connect() as lock_connection:
        acquired = await _wait_for_worker_lock(lock_connection, stop_event)
        if not acquired:
            return

        try:
            if stop_event.is_set():
                return
            lock_backend_pid = await worker_lock_session_id(lock_connection)
            recovered = await recover_interrupted_batches()
            if recovered:
                logger.warning(
                    "Recovered %s interrupted OCR batch(es)",
                    recovered,
                )
            if stop_event.is_set():
                return

            logger.info("Prewarming PaddleOCR")
            await asyncio.to_thread(prewarm_ocr)
            install_sigill_exit_handler()
            logger.info("OCR worker is ready")

            while not stop_event.is_set():
                await verify_worker_lock_session(
                    lock_connection,
                    lock_backend_pid,
                )
                reload_llm_from_env_file()
                processed = await process_next_queued_document()
                if not processed:
                    await _wait_or_stop(stop_event, POLL_INTERVAL_SECONDS)
        finally:
            try:
                await release_worker_lock(lock_connection)
            except Exception:
                logger.exception("Failed to release OCR worker advisory lock")


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            signal.signal(
                sig,
                lambda *_: loop.call_soon_threadsafe(stop_event.set),
            )


async def _main() -> None:
    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)
    await run_worker(stop_event)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(_main())


if __name__ == "__main__":
    main()
