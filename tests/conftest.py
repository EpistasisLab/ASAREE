"""Shared setup for integration tests that use the row-test accounts."""

import os

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from asaree.services.users import create_user, get_user_by_email


@pytest_asyncio.fixture(scope="session")
async def row_test_users() -> None:
    """Create missing test accounts; leave existing credentials and profiles alone."""
    engine = create_async_engine(os.environ["ASAREE_PRODUCT_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as db:
            for email, name in (("test@test.com", "Row Test User"), ("other@test.com", "Row Test Other User")):
                if await get_user_by_email(db, email) is None:
                    await create_user(db, email=email, password="Test1234", display_name=name)
    finally:
        await engine.dispose()
