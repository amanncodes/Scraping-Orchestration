import uuid
from django.db import models


class JobStatus(models.TextChoices):
    QUEUED      = "queued",     "Queued"
    PROCESSING  = "processing", "Processing"
    PAYLOAD     = "payload",    "Payload Ready"
    FAILED      = "failed",     "Failed"
    CACHED      = "cached",     "Cached"


class Job(models.Model):
    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post_id       = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    url           = models.TextField()
    platform      = models.CharField(max_length=32, default="instagram")
    status        = models.CharField(
                        max_length=16,
                        choices=JobStatus.choices,
                        default=JobStatus.QUEUED
                    )
    source        = models.CharField(max_length=16, default="live")
    storage_ref   = models.CharField(max_length=64, null=True, blank=True)
    error_summary = models.TextField(null=True, blank=True)
    created_at    = models.DateTimeField(auto_now_add=True)
    started_at    = models.DateTimeField(null=True, blank=True)
    completed_at  = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "jobs"
        ordering = ["-created_at"]