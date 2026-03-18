import httpx
from django.conf import settings
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import Job, JobLayer, JobStatus
from .serializers import JobSubmitSerializer, JobStatusSerializer
from services.instagram import validate_instagram_url
from services.cache import check_cache, write_cache
from services.payload_store import get_payload
from services.event_logger import log_event, log_error


class JobSubmitView(APIView):

    def post(self, request):
        serializer = JobSubmitSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        url      = serializer.validated_data["url"]
        platform = serializer.validated_data["platform"]  # "instagram" only for now

        # ── Step 1: Extract post_id ───────────────────────────────
        post_id, err = validate_instagram_url(url)
        if err:
            return Response({"error": err}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        log_event("job_received", job_id="pending", platform=platform, post_id=post_id)

        # ── Step 2: Redis cache check (fast path) ─────────────────
        cached = check_cache(post_id, platform)
        if cached:
            # Audit job
            job = Job.objects.create(
                post_id=post_id, url=url, platform=platform,
                status=JobStatus.CACHED, source="cached",
                storage_ref=cached["storage_ref"],
                started_at=timezone.now(), completed_at=timezone.now(),
            )
            payload = get_payload(cached["storage_ref"])
            log_event("cache_hit", job_id=job.id, platform=platform, post_id=post_id)
            return Response({
                "job_id":  str(job.id),
                "status":  "cached",
                "source":  "cached",
                "message": "Data already available. Returning cached result.",
                "payload": payload["data"] if payload else None,
            }, status=status.HTTP_200_OK)

        # ── Step 3: Postgres dedup check ──────────────────────────
        existing = (
            Job.objects
            .filter(post_id=post_id, platform=platform)
            .exclude(status=JobStatus.FAILED)
            .order_by("-created_at")
            .first()
        )

        if existing:
            if existing.status == JobStatus.PAYLOAD:
                payload = get_payload(existing.storage_ref)
                log_event("db_cache_hit", job_id=existing.id, platform=platform, post_id=post_id)
                return Response({
                    "job_id":  str(existing.id),
                    "status":  "cached",
                    "source":  "db_cache",
                    "message": "Data already available. Returning cached result.",
                    "payload": payload["data"] if payload else None,
                }, status=status.HTTP_200_OK)

            if existing.status in (JobStatus.QUEUED, JobStatus.PROCESSING):
                log_event("duplicate_in_progress", job_id=existing.id, platform=platform, post_id=post_id)
                return Response({
                    "job_id":  str(existing.id),
                    "status":  existing.status,
                    "message": "A scrape for this post is already in progress.",
                }, status=status.HTTP_202_ACCEPTED)

        # ── Step 4: Create new job ────────────────────────────────
        job = Job.objects.create(
            post_id=post_id,
            url=url,
            platform=platform,
            status=JobStatus.QUEUED,
            source="live",
        )

        # ── Step 5: Call existing backend ─────────────────────────
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(
                    f"{settings.EXISTING_BACKEND_URL}/posts/scrape",
                    json={
                        "post_uri":     url,
                        "callback_url": settings.SOP_WEBHOOK_URL,
                    }
                )
                resp.raise_for_status()
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error_summary = f"Could not reach scraping backend: {str(e)}"
            job.completed_at = timezone.now()
            job.save()
            log_error("backend_unreachable", job_id=job.id, platform=platform, error=str(e))
            return Response(
                {"error": "Scraping service unreachable. Please try again."},
                status=status.HTTP_502_BAD_GATEWAY
            )

        log_event("job_queued", job_id=job.id, platform=platform, post_id=post_id)

        return Response({
            "job_id":  str(job.id),
            "status":  "queued",
            "message": f"Scrape job queued for instagram post {post_id}.",
        }, status=status.HTTP_202_ACCEPTED)


class JobStatusView(APIView):

    def get(self, request, job_id):
        try:
            job = Job.objects.prefetch_related("layers").get(id=job_id)
        except Job.DoesNotExist:
            return Response({"error": "Job not found."}, status=status.HTTP_404_NOT_FOUND)

        data = JobStatusSerializer(job).data

        # Attach payload if ready
        if job.status == JobStatus.PAYLOAD and job.storage_ref:
            payload_doc = get_payload(job.storage_ref)
            data["payload"] = payload_doc["data"] if payload_doc else None

        # Human-readable message per status
        messages = {
            JobStatus.QUEUED:     "Job is queued and will begin shortly.",
            JobStatus.PROCESSING: f"Scraping in progress via {job.current_layer or 'scraper'}.",
            JobStatus.PAYLOAD:    "Scrape complete. Payload is ready.",
            JobStatus.FAILED:     "Scrape failed. " + (job.error_summary or ""),
            JobStatus.CACHED:     "Data returned from cache.",
        }
        data["message"] = messages.get(job.status, "")

        return Response(data, status=status.HTTP_200_OK)