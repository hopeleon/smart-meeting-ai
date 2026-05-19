import sys
sys.path.insert(0, '.')

from app.main import app
from fastapi.routing import APIRoute

print("=== 所有注册的路由 ===")
for route in app.routes:
    if isinstance(route, APIRoute):
        methods = ','.join(route.methods)
        print(f"{methods:10} {route.path}")
