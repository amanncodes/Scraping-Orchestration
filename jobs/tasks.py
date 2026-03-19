import httpx
import logging
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from services.payload_store import mark_sent
from services.event_logger import log_event, log_error

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    queue="sop.webhook",
    max_retries=3,
    default_retry_delay=30,
)
def deliver_webhook(self, job_id: str, storage_ref: str, payload: dict):
    """
    Deliver scraped payload to the caller's configured webhook URL.
    Retries: 30s → 2min → 10min then gives up.
    """
    from jobs.models import Job
    try:
        job = Job.objects.get(id=job_id)
    except Job.DoesNotExist:
        return

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
        mark_sent(storage_ref)
        log_event("webhook_delivered", job_id=job_id, platform=job.platform)
    except Exception as exc:
        log_error("webhook_delivery_failed", job_id=job_id,
                  platform=job.platform, error=str(exc),
                  retry=self.request.retries)
        countdown = 30 * (4 ** self.request.retries)  # 30s → 2min → 10min
        raise self.retry(exc=exc, countdown=countdown)


@shared_task(
    bind=True,
    queue="sop.scrape",
    max_retries=0,
)
def fallback_to_login_bot(self, job_id: str, post_url: str, platform: str):
    """
    Celery fallback task — fires SQS_FALLBACK_TIMEOUT seconds after a job
    is sent to SQS. If Lambda already delivered the payload (job status is
    PAYLOAD or FAILED), this task does nothing. If the job is still
    PROCESSING (Lambda never called back), it triggers login-bot-1 as
    a fallback scraper.

    This ensures every job eventually gets scraped — either by Lambda
    (fast, GraphQL) or by login-bot-1 (cookie-based DOM scraper).
    """
    from jobs.models import Job, JobStatus

    try:
        job = Job.objects.get(id=job_id)
    except Job.DoesNotExist:
        logger.warning(f"fallback_to_login_bot: job {job_id} not found, skipping")
        return

    # ── Check if Lambda already delivered ────────────────────────
    if job.status in (JobStatus.PAYLOAD, JobStatus.FAILED, JobStatus.CACHED):
        logger.info(
            f"fallback_to_login_bot: job {job_id} already has status "
            f"'{job.status}' — Lambda succeeded, no fallback needed."
        )
        return

    # ── Job still processing — Lambda timed out, trigger fallback ─
    logger.warning(
        f"fallback_to_login_bot: job {job_id} still '{job.status}' after "
        f"{settings.SQS_FALLBACK_TIMEOUT}s — triggering login-bot-1 fallback."
    )

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{settings.SCRAPER_URL}/webhook/trigger-job/",
                json={
                    "job_id":       job_id,
                    "platform":     platform,
                    "post_url":     post_url,
                    "callback_url": settings.SOP_WEBHOOK_URL,
                }
            )
            resp.raise_for_status()

        # Update source to reflect fallback path was used
        job.source = "login_bot_fallback"
        job.save(update_fields=["source"])

        logger.info(
            f"fallback_to_login_bot: login-bot-1 accepted job {job_id} "
            f"as fallback scraper."
        )
        log_event(
            "sqs_fallback_triggered",
            job_id=job_id,
            platform=platform,
            fallback="login_bot",
        )

    except Exception as exc:
        # login-bot-1 also failed — mark job as failed
        logger.error(
            f"fallback_to_login_bot: login-bot-1 also failed for job "
            f"{job_id}: {exc}"
        )
        job.status        = JobStatus.FAILED
        job.error_summary = (
            f"Lambda timed out after {settings.SQS_FALLBACK_TIMEOUT}s "
            f"and login-bot-1 fallback also failed: {str(exc)}"
        )
        job.completed_at = timezone.now()
        job.save()
        log_error(
            "all_scrapers_failed",
            job_id=job_id,
            platform=platform,
            error=str(exc),
        )