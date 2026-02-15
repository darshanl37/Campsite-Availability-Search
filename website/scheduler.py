"""
APScheduler setup with SQLAlchemy job store.

Jobs persist across server restarts.  The scheduler is initialised once
from app.py after the database has been created.
"""

import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor

logger = logging.getLogger(__name__)

scheduler = None  # module-level singleton


def init_scheduler(database_uri):
    """Create and start the BackgroundScheduler.

    Call this once from app.py after ``db.create_all()``.
    """
    global scheduler
    if scheduler is not None:
        return scheduler

    jobstores = {
        'default': SQLAlchemyJobStore(url=database_uri),
    }
    executors = {
        'default': ThreadPoolExecutor(max_workers=5),
    }
    job_defaults = {
        'coalesce': True,       # combine missed runs into one
        'max_instances': 1,     # only one instance of each job at a time
        'misfire_grace_time': 300,
    }

    scheduler = BackgroundScheduler(
        jobstores=jobstores,
        executors=executors,
        job_defaults=job_defaults,
    )
    scheduler.start()
    logger.info("APScheduler started")
    return scheduler


def get_scheduler():
    """Return the running scheduler instance."""
    return scheduler
