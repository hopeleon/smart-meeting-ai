import asyncio
from app.database import engine
from app.models import Base

async def check():
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: print("Tables in metadata:", list(Base.metadata.tables.keys())))
        from sqlalchemy import text
        result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"))
        tables = [row[0] for row in result.fetchall()]
        print("Tables in DB:", tables)

asyncio.run(check())
