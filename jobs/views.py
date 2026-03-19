import json
import httpx
import boto3
from django.conf import settings
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status as http_status

from .models import Job, JobStatus
from .serializers import SubmitSerializer, StatusSerializer
from services.instagram import extract_post_id
from services.cache import check as cache_check, write as cache_write
from services.payload_store import fetch as payload_fetch


def _push_to_sqs(job_id: str, post_url: str, callback_url: str) -> bool:
    """
    Push a scrape job to AWS SQS.
    Lambda picks up the message, scrapes using GraphQL API,
    and POSTs results to callback_url.
    Returns True if message was sent successfully, False otherwise.
    """
    try:
        sqs = boto3.client(
            "sqs",
            region_name=settings.AWS_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )
        message = {
            "job_id":       job_id,
            "post_url":     post_url,
            "callback_url": callback_url,
        }
        sqs.send_message(
            QueueUrl=settings.SQS_QUEUE_URL,
            MessageBody=json.dumps(message),
        )
        return True
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            f"SQS push failed for job {job_id}: {e}"
        )
        return False


def _trigger_login_bot(job_id: str, post_url: str, callback_url: str, platform: str) -> bool:
    """
    Trigger login-bot-1 as fallback scraper.
    Uses cookie-based DOM scraping via office PC.
    Returns True if login-bot accepted the job, False otherwise.
    """
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{settings.SCRAPER_URL}/webhook/trigger-job/",
                json={
                    "job_id":       job_id,
                    "platform":     platform,
                    "post_url":     post_url,
                    "callback_url": callback_url,
                }
            )
            resp.raise_for_status()
            return True
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(
            f"login-bot-1 fallback failed for job {job_id}: {e}"
        )
        return False


class JobSubmitView(APIView):

    def post(self, request):
        ser = SubmitSerializer(data=request.data)
        if not ser.is_valid():
            return Response(ser.errors, status=http_status.HTTP_400_BAD_REQUEST)

        url      = ser.validated_data["url"]
        platform = ser.validated_data["platform"]

        # ── 1. Extract post ID via regex ──────────────────────────
        post_id = extract_post_id(url)
        if not post_id:
            return Response(
                {"error": (
                    "Cannot extract a valid Instagram post ID from this URL. "
                    "Supported: /p/{id}/, /reel/{id}/, /tv/{id}/"
                )},
                status=http_status.HTTP_422_UNPROCESSABLE_ENTITY
            )

        # ── 2. Redis cache check ──────────────────────────────────
        cached = cache_check(post_id)
        if cached:
            job = Job.objects.create(
                post_id=post_id, url=url, platform=platform,
                status=JobStatus.CACHED, source="cached",
                storage_ref=cached["storage_ref"],
                started_at=timezone.now(), completed_at=timezone.now(),
            )
            payload_doc = payload_fetch(cached["storage_ref"])
            return Response({
                "job_id":  str(job.id),
                "status":  "cached",
                "source":  "redis_cache",
                "message": "Already scraped. Returning cached payload.",
                "payload": payload_doc["data"] if payload_doc else None,
            }, status=http_status.HTTP_200_OK)

        # ── 3. Postgres dedup check ───────────────────────────────
        existing = (
            Job.objects
            .filter(post_id=post_id, platform=platform)
            .exclude(status=JobStatus.FAILED)
            .order_by("-created_at")
            .first()
        )

        if existing:
            if existing.status == JobStatus.PAYLOAD:
                payload_doc = payload_fetch(existing.storage_ref)
                return Response({
                    "job_id":  str(existing.id),
                    "status":  "cached",
                    "source":  "db_cache",
                    "message": "Already scraped. Returning cached payload.",
                    "payload": payload_doc["data"] if payload_doc else None,
                }, status=http_status.HTTP_200_OK)

            if existing.status in (JobStatus.QUEUED, JobStatus.PROCESSING):
                return Response({
                    "job_id":  str(existing.id),
                    "status":  existing.status,
                    "message": "Scrape already in progress for this post.",
                }, status=http_status.HTTP_202_ACCEPTED)

        # ── 4. Create new job ─────────────────────────────────────
        job = Job.objects.create(
            post_id=post_id,
            url=url,
            platform=platform,
            status=JobStatus.QUEUED,
            source="live",
        )

        # ── 5. Try SQS (Lambda) first ─────────────────────────────
        sqs_ok = _push_to_sqs(
            job_id=str(job.id),
            post_url=url,
            callback_url=settings.SOP_WEBHOOK_URL,
        )

        if sqs_ok:
            # SQS accepted — update job and schedule Celery fallback timer.
            # If Lambda doesn't callback within SQS_FALLBACK_TIMEOUT seconds,
            # Celery fires login-bot-1 automatically as fallback.
            job.status     = JobStatus.PROCESSING
            job.started_at = timezone.now()
            job.source     = "sqs"
            job.save()

            from jobs.tasks import fallback_to_login_bot
            fallback_to_login_bot.apply_async(
                args=[str(job.id), url, platform],
                countdown=settings.SQS_FALLBACK_TIMEOUT,
            )

            return Response({
                "job_id":  str(job.id),
                "status":  "queued",
                "source":  "sqs",
                "message": (
                    f"Scrape job sent to Lambda via SQS for post {post_id}. "
                    f"Fallback to login-bot-1 in {settings.SQS_FALLBACK_TIMEOUT}s "
                    f"if no response."
                ),
            }, status=http_status.HTTP_202_ACCEPTED)

        # ── 6. SQS failed — fall back to login-bot-1 immediately ──
        login_bot_ok = _trigger_login_bot(
            job_id=str(job.id),
            post_url=url,
            callback_url=settings.SOP_WEBHOOK_URL,
            platform=platform,
        )

        if login_bot_ok:
            job.status     = JobStatus.PROCESSING
            job.started_at = timezone.now()
            job.source     = "login_bot_immediate"
            job.save()

            return Response({
                "job_id":  str(job.id),
                "status":  "queued",
                "source":  "login_bot",
                "message": (
                    f"SQS unavailable. Scrape job relayed directly to login-bot-1 "
                    f"for post {post_id}."
                ),
            }, status=http_status.HTTP_202_ACCEPTED)

        # ── 7. Both paths failed ──────────────────────────────────
        job.status        = JobStatus.FAILED
        job.error_summary = "Both SQS and login-bot-1 are unreachable."
        job.completed_at  = timezone.now()
        job.save()

        return Response(
            {"error": "All scrapers unreachable. Job marked as failed."},
            status=http_status.HTTP_502_BAD_GATEWAY
        )


class JobStatusView(APIView):

    def get(self, request, job_id):
        try:
            job = Job.objects.get(id=job_id)
        except Job.DoesNotExist:
            return Response(
                {"error": "Job not found."},
                status=http_status.HTTP_404_NOT_FOUND
            )

        data = StatusSerializer(job).data

        if job.status == JobStatus.PAYLOAD and job.storage_ref:
            payload_doc = payload_fetch(job.storage_ref)
            data["payload"] = payload_doc["data"] if payload_doc else None

        messages = {
            JobStatus.QUEUED:     "Job queued. Scraper is being contacted.",
            JobStatus.PROCESSING: "Scraper is running.",
            JobStatus.PAYLOAD:    "Scrape complete. Payload ready.",
            JobStatus.FAILED:     f"Failed. {job.error_summary or ''}",
            JobStatus.CACHED:     "Returned from cache.",
        }
        data["message"] = messages.get(job.status, "")

        return Response(data)