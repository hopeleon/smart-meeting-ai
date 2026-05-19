import requests
import json

r = requests.get('http://127.0.0.1:8000/api/speakers', timeout=30)
print(f'Status: {r.status_code}')
data = r.json()
print(f'Total: {data.get("total", 0)}')
print(f'Speakers count: {len(data.get("speakers", []))}')
print(f'Stats: total={data["stats"]["total"]}, active={data["stats"]["active"]}')
if data.get('speakers'):
    print(f'\n前3条数据:')
    for sp in data['speakers'][:3]:
        print(f'  - {sp["name"]}: {sp["speaker_id"]}, 质量: {sp["quality"]}')
