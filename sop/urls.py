from django.urls import path, include

urlpatterns = [
    path("api/v1/", include("jobs.urls")),
    path("api/v1/", include("webhook.urls")),
]