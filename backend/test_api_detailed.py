import requests

try:
    # 测试健康检查
    r_health = requests.get('http://localhost:8000/health', timeout=5)
    print(f'Health check: {r_health.status_code} - {r_health.json()}')

    # 测试 speakers API
    r = requests.get('http://localhost:8000/api/speakers', timeout=30)
    print(f'Speakers API: {r.status_code}')
    print(f'Content type: {r.headers.get("content-type")}')
    print(f'Response: {r.text[:500]}')
except Exception as e:
    print(f'Error: {type(e).__name__}: {e}')
