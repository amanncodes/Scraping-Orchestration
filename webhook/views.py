from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status as http_status

from jobs.models import Job, JobStatus
from services.payload_store import store as payload_store, mark_sent
from services.cache import write as cache_write


class WebhookReceiveView(APIView):
    """
    Receives callback from your office Instagram scraper
    after scraping completes.

    Expected payload shapes:

    Shape A — Instagram with retry_loop (your existing scraper format):
    {
        "job_id": "uuid",
        "success": true,
        "retry_loop": false,
        "comments": [...],
        "error": null
    }

    Shape B — Generic array format:
    [
        {"job_id": "uuid", "success": true, "comments": [...]}
    ]

    Shape C — Direct payload (scraper returns data directly, no wrapping):
    {
        "job_id": "uuid",
        "data": {...}
    }
    """

    def post(self, request):
        payload = request.data

        # ── Resolve job_id ────────────────────────────────────────
        if isinstance(payload, list):
            job_id = payload[0].get("job_id") if payload else None
            raw    = payload
        elif isinstance(payload, dict):
            job_id = payload.get("job_id")
            raw    = payload
        else:
            return Response(
                {"error": "Unrecognised payload format."},
                status=http_status.HTTP_400_BAD_REQUEST
            )

        if not job_id:
            return Response(
                {"error": "Missing job_id in payload."},
                status=http_status.HTTP_400_BAD_REQUEST
            )

        try:
            job = Job.objects.get(id=job_id)
        except Job.DoesNotExist:
            return Response(
                {"error": "Job not found."},
                status=http_status.HTTP_404_NOT_FOUND
            )

        # ── Handle failure ────────────────────────────────────────
        success = payload.get("success", True) if isinstance(payload, dict) else True
        if not success:
            job.status = JobStatus.FAILED
            job.error_summary = (
                payload.get("error", "Scraper returned failure")
                if isinstance(payload, dict) else "Scraper returned failure"
            )
            job.completed_at = timezone.now()
            job.save()
            return Response({"message": "Recorded as failed."})

        # ── Store payload in MongoDB ──────────────────────────────
        storage_ref = payload_store(str(job.id), job.platform, raw)

        job.status       = JobStatus.PAYLOAD
        job.storage_ref  = storage_ref
        job.completed_at = timezone.now()
        job.save()

        # ── Write Redis cache ─────────────────────────────────────
        if job.post_id:
            cache_write(job.post_id, storage_ref)

        mark_sent(storage_ref)

        return Response({"message": "Payload received and stored."}, status=http_status.HTTP_200_OK)