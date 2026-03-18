from rest_framework import serializers
from .models import Job, JobLayer


class JobLayerSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobLayer
        fields = ["layer_name", "layer_order", "status",
                  "error_type", "error_message", "started_at", "completed_at"]


class JobSubmitSerializer(serializers.Serializer):
    url      = serializers.URLField()
    platform = serializers.ChoiceField(choices=["instagram"])


class JobStatusSerializer(serializers.ModelSerializer):
    layers = JobLayerSerializer(many=True, read_only=True)

    class Meta:
        model = Job
        fields = ["id", "post_id", "url", "platform", "status",
                  "source", "current_layer", "error_summary",
                  "created_at", "started_at", "completed_at", "layers"]