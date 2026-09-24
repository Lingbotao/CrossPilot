"""异步任务层（Celery 5 + Beat）。"""

from app.tasks.celery_app import celery_app

__all__ = ["celery_app"]
