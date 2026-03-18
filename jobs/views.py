import httpx
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

        # ── 4. New job ────────────────────────────────────────────
        job = Job.objects.create(
            post_id=post_id,
            url=url,
            platform=platform,
            status=JobStatus.QUEUED,
            source="live",
        )

        # ── 5. Relay to office scraper ────────────────────────────
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(
                    f"{settings.SCRAPER_URL}/posts/scrape",
                    json={
                        "post_uri":     url,
                        "callback_url": settings.SOP_WEBHOOK_URL,
                        "job_id":       str(job.id),
                    }
                )
                resp.raise_for_status()
                job.status = JobStatus.PROCESSING
                job.started_at = timezone.now()
                job.save()

        except Exception as e:
            job.status = JobStatus.FAILED
            job.error_summary = f"Could not reach scraper: {str(e)}"
            job.completed_at = timezone.now()
            job.save()
            return Response(
                {"error": f"Scraper unreachable: {str(e)}"},
                status=http_status.HTTP_502_BAD_GATEWAY
            )

        return Response({
            "job_id":  str(job.id),
            "status":  "queued",
            "message": f"Scrape job relayed to scraper for post {post_id}.",
        }, status=http_status.HTTP_202_ACCEPTED)


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