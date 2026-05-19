import sys
sys.path.insert(0, '.')
from app.services.speaker_db_service import get_speaker_db

db = get_speaker_db()
conn = db._conn()
row = conn.execute("SELECT COUNT(*) FROM speaker_profiles").fetchone()
print(f"row type: {type(row)}, value: {row}")
print(f"row[0]: {row[0]}")

# 模拟 safe_int 的行为
value = row  # 传入整个 row 元组
print(f"\n模拟 safe_int:")
print(f"int(value): ", end="")
try:
    print(int(value))
except TypeError as e:
    print(f"TypeError! - {e}")

print(f"\n正确的做法:")
print(f"int(value[0]): {int(row[0])}")
conn.close()
