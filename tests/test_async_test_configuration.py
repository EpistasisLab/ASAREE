"""Regression tests for the event-loop contract used by async integration tests."""

import asyncio

import pytest_asyncio


@pytest_asyncio.fixture
async def fixture_event_loop() -> asyncio.AbstractEventLoop:
    return asyncio.get_running_loop()


async def test_async_fixtures_and_tests_share_the_session_loop(
    fixture_event_loop: asyncio.AbstractEventLoop,
) -> None:
    assert fixture_event_loop is asyncio.get_running_loop()
