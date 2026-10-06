"""Create or refresh the persistent development users used by row tests."""

from __future__ import annotations

import asyncio

from asaree.models.database import dispose_engine, get_session
from asaree.services.users import create_user, get_user_by_email, set_password

PASSWORD = "Test1234"
USERS = (
    ("test@test.com", "Row Test User"),
    ("other@test.com", "Row Test Other User"),
)


async def seed() -> None:
    try:
        async with get_session() as db:
            for email, display_name in USERS:
                user = await get_user_by_email(db, email)
                if user is None:
                    user = await create_user(db, email=email, password=PASSWORD, display_name=display_name)
                else:
                    await set_password(db, user, new_password=PASSWORD)
                    user.display_name = display_name
                    user.is_active = True
            print("Seeded row-test users: test@test.com, other@test.com")
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(seed())
