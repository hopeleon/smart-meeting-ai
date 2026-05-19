import requests

try:
    r = requests.get('http://localhost:8000/api/speakers', timeout=10)
    print(f'Status: {r.status_code}')
    data = r.json()
    print(f'Total speakers: {data.get("total", 0)}')
    print(f'Speakers count: {len(data.get("speakers", []))}')
    if data.get('speakers'):
        print(f'First: {data["speakers"][0]["name"]} - {data["speakers"][0]["speaker_id"]}')
except Exception as e:
    print(f'Error: {e}')
