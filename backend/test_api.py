import sys
sys.path.insert(0, '.')
from app.services.speaker_db_service import get_speaker_db
from app.api.speakers import _safe_profile, _safe_stats

db = get_speaker_db()
speakers = db.load_all(active_only=True)
stats = db.get_stats()

print('=== 数据库查询结果 ===')
print(f'数据库说话人数量: {len(speakers)}')
print(f'统计信息:')
print(f'  - 总人数: {stats["total"]}')
print(f'  - 活跃: {stats["active"]}')
print(f'  - 数据库路径: {stats["db_path"]}')

# 测试 API 响应格式
safe_speakers = [_safe_profile(s) for s in speakers[:3]]
print('\nAPI 响应示例 (前3条):')
for sp in safe_speakers:
    print(f'  - {sp["name"]}: {sp["speaker_id"]}, 质量: {sp["quality"]}')

print('\n=== 测试通过 ===')
