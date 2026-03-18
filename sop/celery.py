import os
from celery import Celery
from kombu import Queue

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sop.settings")

app = Celery("sop")
app.config_from_object("django.conf:settings", namespace="CELERY")

# SOP's own queues — on the EXISTING shared Redis broker
# Prefixed with "sop." to never collide with existing scraper queues
app.conf.task_queues = (
    Queue("sop.scrape"),
    Queue("sop.webhook"),
)
app.conf.task_default_queue = "sop.scrape"
app.autodiscover_tasks()