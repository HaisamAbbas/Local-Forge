"""FastAPI dependencies for request-scoped local services."""

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Annotated, cast
from uuid import UUID

from fastapi import Depends, Request

from localforge.adapters.catalog import AdapterCatalog
from localforge.storage.database import open_database


def database_connection(request: Request) -> Iterator[sqlite3.Connection]:
    """Open a short-lived connection to the user's own LOCALFORGE database."""

    database_path = cast(Path, request.app.state.database_path)
    connection = open_database(database_path)
    try:
        yield connection
    finally:
        connection.close()


def adapter_catalog(request: Request) -> AdapterCatalog:
    """Return the process-local catalog initialized by the application lifespan."""

    return cast(AdapterCatalog, request.app.state.adapter_catalog)


async def engine_profile_mutation_lock(profile_id: UUID, request: Request) -> AsyncIterator[None]:
    """Serialize operations that can start, stop, edit, probe, or remove one profile."""

    locks = cast(dict[UUID, asyncio.Lock], request.app.state.engine_profile_lifecycle_locks)
    lock = locks.setdefault(profile_id, asyncio.Lock())
    async with lock:
        yield


DatabaseConnection = Annotated[sqlite3.Connection, Depends(database_connection)]
LocalAdapterCatalog = Annotated[AdapterCatalog, Depends(adapter_catalog)]
EngineProfileMutationLock = Annotated[None, Depends(engine_profile_mutation_lock)]
