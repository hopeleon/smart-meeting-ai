from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

from app.database import get_db


async def get_database(session: AsyncSession = Depends(get_db)) -> AsyncSession:
    return session
