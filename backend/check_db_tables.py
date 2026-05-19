import asyncio
from app.database import engine

async def check():
    async with engine.begin() as conn:
        from sqlalchemy import text
        result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"))
        tables = [row[0] for row in result.fetchall()]
        print("Tables:", tables)
        if "period_summaries" in tables:
            result2 = await conn.execute(text("SELECT COUNT(*) FROM period_summaries"))
            print(f"period_summaries rows: {result2.scalar()}")
        else:
            print("WARNING: period_summaries table NOT FOUND!")

asyncio.run(check())
