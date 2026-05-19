import sqlite3

# 检查 local.db
print('=== local.db ===')
conn = sqlite3.connect('f:/smart-meeting-ai/backend/local.db')
cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
print('Tables:', [r[0] for r in cur.fetchall()])

# 检查是否有 speaker 相关表
for table in ['speaker_profiles', 'speakers', 'speaker_embeddings', 'employee_voiceprints']:
    try:
        cur = conn.execute(f'SELECT COUNT(*) FROM {table}')
        count = cur.fetchone()[0]
        print(f'{table}: {count} 条记录')
    except Exception as e:
        pass
conn.close()

print()
print('=== speaker_voiceprints.db ===')
conn = sqlite3.connect('f:/smart-meeting-ai/backend/data/speaker_voiceprints.db')
cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
print('Tables:', [r[0] for r in cur.fetchall()])
cur = conn.execute('SELECT COUNT(*) FROM speaker_profiles')
print(f'speaker_profiles: {cur.fetchone()[0]} 条记录')
cur = conn.execute('SELECT COUNT(*) FROM speaker_embeddings')
print(f'speaker_embeddings: {cur.fetchone()[0]} 条记录')
conn.close()
