from celery import Celery
from app.config import settings

is_local = settings.ENV == "local"

if is_local:
    # 本地模式：不需要 Redis，任务同步执行
    celery_app = Celery(
        "meeting_platform",
        broker="memory://",
        backend="cache+memory://",
    )
    celery_app.conf.update(
        task_always_eager=True,
        task_eager_propagates=True,
    )
else:
    celery_app = Celery(
        "meeting_platform",
        broker=settings.CELERY_BROKER_URL,
        backend=settings.CELERY_RESULT_BACKEND,
    )

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# 自动发现任务模块
celery_app.autodiscover_tasks(["app.workers"])
