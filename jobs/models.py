import uuid
from django.db import models


class JobStatus(models.TextChoices):
    QUEUED      = "queued",      "Queued"
    PROCESSING  = "processing",  "Processing"
    PAYLOAD     = "payload",     "Payload Ready"
    FAILED      = "failed",      "Failed"
    CACHED      = "cached",      "Cached"


class Job(models.Model):
    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post_id       = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    url           = models.TextField()
    platform      = models.CharField(max_length=32)
    status        = models.CharField(max_length=16, choices=JobStatus.choices, default=JobStatus.QUEUED)
    source        = models.CharField(max_length=16, default="live")  # live | cached | db_cache
    current_layer = models.CharField(max_length=64, null=True, blank=True)
    error_summary = models.TextField(null=True, blank=True)
    storage_ref   = models.CharField(max_length=64, null=True, blank=True)  # MongoDB _id
    created_at    = models.DateTimeField(auto_now_add=True)
    started_at    = models.DateTimeField(null=True, blank=True)
    completed_at  = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "jobs"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.platform} | {self.post_id} | {self.status}"


class JobLayer(models.Model):
    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job           = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="layers")
    layer_name    = models.CharField(max_length=64)
    layer_order   = models.IntegerField(default=1)
    status        = models.CharField(max_length=16)  # running | completed | failed
    error_type    = models.CharField(max_length=64, null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    started_at    = models.DateTimeField(auto_now_add=True)
    completed_at  = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "job_layers"