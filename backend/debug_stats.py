import sys
sys.path.insert(0, '.')
from app.services.speaker_db_service import get_speaker_db

db = get_speaker_db()

# 直接调试 SQL 查询
conn = db._conn()
total = conn.execute("SELECT COUNT(*) FROM speaker_profiles").fetchone()
active = conn.execute("SELECT COUNT(*) FROM speaker_profiles WHERE is_active = 1").fetchone()
print(f"SQL COUNT(*) total: {total[0]}")
print(f"SQL COUNT(*) active: {active[0]}")

# 检查数据类型
print(f"\n检查数据类型:")
row = conn.execute("SELECT is_active FROM speaker_profiles LIMIT 1").fetchone()
print(f"is_active type: {type(row['is_active'])}, value: {row['is_active']}")
conn.close()

# 调用 get_stats
stats = db.get_stats()
print(f"\nget_stats() 返回:")
print(f"  total: {stats['total']}")
print(f"  active: {stats['active']}")
