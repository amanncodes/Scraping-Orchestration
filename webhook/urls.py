from django.urls import path
from .views import WebhookReceiveView

urlpatterns = [
    path("webhook/receive", WebhookReceiveView.as_view(), name="webhook-receive"),
]