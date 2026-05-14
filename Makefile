.PHONY: dev local migrate seed test lint build down logs

# 本地启动（不需要 Docker，SQLite + eager Celery）
local:
	bash start-local.sh

# Docker 开发环境启动（全部 stub 服务）
dev:
	docker compose up --build

# 停止所有服务
down:
	docker compose down

# 查看日志
logs:
	docker compose logs -f

# 运行数据库迁移
migrate:
	docker compose exec backend alembic upgrade head

# 创建新的迁移文件
migration:
	docker compose exec backend alembic revision --autogenerate -m "$(msg)"

# 插入测试数据
seed:
	docker compose exec backend python -m app.seed

# 运行后端测试
test:
	docker compose exec backend pytest -v

# 运行后端测试（本地）
test-local:
	cd backend && pytest -v

# 代码检查
lint:
	cd backend && mypy app/ && ruff check app/
	cd frontend && npx tsc --noEmit

# 构建生产镜像
build:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml build

# 仅启动基础设施
infra:
	docker compose up -d postgres redis

# 重新构建单个服务
rebuild-%:
	docker compose up -d --build $*
