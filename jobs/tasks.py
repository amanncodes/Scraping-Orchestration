import httpx
from celery import shared_task
from django.conf import settings
from services.payload_store import mark_webhook_sent
from services.event_logger import log_event, log_error


@shared_task(
    bind=True,
    queue="sop.webhook",
    max_retries=3,
    default_retry_delay=30,
)
def deliver_webhook(self, job_id: str, storage_ref: str, payload: dict):
    """
    Deliver scraped payload to the caller's configured webhook.
    Retries: 30s → 2min → 10min then gives up.
    """
    from jobs.models import Job
    try:
        job = Job.objects.get(id=job_id)
    except Job.DoesNotExist:
        return

    # For now SOP_WEBHOOK_URL is the outbound destination.
    # When per-job callback_url is implemented, use job.callback_url instead.
    callback_url = settings.SOP_WEBHOOK_URL

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(callback_url, json={
                "job_id":   job_id,
                "platform": job.platform,
                "post_id":  job.post_id,
                "status":   "payload",
                "data":     payload,
            })
            resp.raise_for_status()

        mark_webhook_sent(storage_ref)
        log_event("webhook_delivered", job_id=job_id, platform=job.platform)

    except Exception as exc:
        log_error("webhook_delivery_failed", job_id=job_id,
                  platform=job.platform, error=str(exc),
                  retry=self.request.retries)
        countdown = 30 * (4 ** self.request.retries)  # 30s → 2min → 10min
        raise self.retry(exc=exc, countdown=countdown)