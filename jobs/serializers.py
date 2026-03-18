from rest_framework import serializers
from .models import Job


class SubmitSerializer(serializers.Serializer):
    url      = serializers.URLField()
    platform = serializers.ChoiceField(choices=["instagram"])


class StatusSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Job
        fields = [
            "id", "post_id", "url", "platform",
            "status", "source", "error_summary",
            "created_at", "started_at", "completed_at",
        ]