import requests

# 用 127.0.0.1 明确指定 IPv4
urls_to_test = [
    'http://127.0.0.1:8000/api/speakers',
    'http://127.0.0.1:8000/api/health',
    'http://localhost:8000/api/speakers',
]

for url in urls_to_test:
    try:
        r = requests.get(url, timeout=5)
        print(f'{url}: {r.status_code}')
    except Exception as e:
        print(f'{url}: ERROR - {e}')
