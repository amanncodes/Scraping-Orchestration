from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from jobs.models import Job, JobLayer, JobStatus
from services.payload_store import store_payload, mark_webhook_sent
from services.cache import write_cache
from services.event_logger import log_event, log_error
from .tasks import deliver_webhook


class WebhookReceiveView(APIView):
    """
    Receives callback from the existing Node.js backend
    after scraping completes.
    """

    def post(self, request):
        payload = request.data

        # ── Detect payload type ───────────────────────────────────
        is_instagram = (
            isinstance(payload, dict) and
            "retry_loop" in payload and
            "comments" in payload
        )
        is_standard = isinstance(payload, list)

        # ── Resolve job ───────────────────────────────────────────
        job_id = (
            payload.get("job_id") if isinstance(payload, dict)
            else payload[0].get("job_id") if payload else None
        )

        if not job_id:
            return Response({"error": "Missing job_id"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            job = Job.objects.get(id=job_id)
        except Job.DoesNotExist:
            return Response({"error": "Job not found"}, status=status.HTTP_404_NOT_FOUND)

        job.started_at = job.started_at or timezone.now()

        # ── Instagram payload ─────────────────────────────────────
        if is_instagram:
            success  = payload.get("success", True)
            comments = payload.get("comments", [])
            retry    = payload.get("retry_loop", False)

            if not success:
                job.status = JobStatus.FAILED
                job.error_summary = payload.get("error", "Scraper returned failure")
                job.completed_at = timezone.now()
                job.save()
                log_error("scrape_failed", job_id=job.id, platform="instagram",
                          error=job.error_summary)
                return Response({"message": "Recorded as failed"})

            if not comments:
                job.status = JobStatus.PAYLOAD
                job.completed_at = timezone.now()
                job.save()
                log_event("scrape_empty", job_id=job.id, platform="instagram")
                return Response({"message": "No comments, marked complete"})

            # Has comments — store and deliver
            storage_ref = store_payload(str(job.id), "instagram", payload)
            job.storage_ref = storage_ref

            if retry:
                job.status = JobStatus.PAYLOAD  # partial but usable
                log_event("scrape_partial", job_id=job.id, platform="instagram")
            else:
                job.status = JobStatus.PAYLOAD
                log_event("scrape_complete", job_id=job.id, platform="instagram",
                          comment_count=len(comments))

            job.completed_at = timezone.now()
            job.save()

            # Write Redis cache
            write_cache(job.post_id, storage_ref, None, "instagram")

            # Create layer record
            JobLayer.objects.create(
                job=job, layer_name="dom_scrape" if not retry else "hikerapi",
                layer_order=1, status="completed",
                completed_at=timezone.now(),
            )

            # Deliver to downstream webhook async
            deliver_webhook.apply_async(
                args=[str(job.id), storage_ref, payload],
                queue="sop.webhook"
            )

        # ── Standard payload (other platforms — future) ───────────
        elif is_standard:
            if not payload:
                job.status = JobStatus.PAYLOAD
            else:
                success = payload[0].get("success", True)
                if not success:
                    job.status = JobStatus.FAILED
                    job.error_summary = "Scraper returned failure"
                else:
                    storage_ref = store_payload(str(job.id), job.platform, {"items": payload})
                    job.storage_ref = storage_ref
                    job.status = JobStatus.PAYLOAD
                    write_cache(job.post_id, storage_ref, None, job.platform)
                    deliver_webhook.apply_async(
                        args=[str(job.id), storage_ref, payload],
                        queue="sop.webhook"
                    )

            job.completed_at = timezone.now()
            job.save()

        else:
            return Response({"error": "Unrecognised payload format"},
                            status=status.HTTP_400_BAD_REQUEST)

        return Response({"message": "Processed successfully"}, status=status.HTTP_200_OK)