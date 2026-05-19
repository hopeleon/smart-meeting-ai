import sys
sys.path.insert(0, '.')
from app.services.speaker_db_service import get_speaker_db

db = get_speaker_db()
print('DB Path:', db.db_path)

speakers = db.load_all(active_only=True)
print(f'加载的说话人数量: {len(speakers)}')

if speakers:
    sp = speakers[0]
    print('第一条记录:')
    print('  speaker_id:', sp.get('speaker_id'))
    print('  name:', sp.get('name'))
    emb = sp.get('embedding')
    print('  embedding:', emb is not None)
    if emb is not None:
        print('  embedding shape:', emb.shape)
else:
    print('!!! 没有加载到任何说话人 !!!')
