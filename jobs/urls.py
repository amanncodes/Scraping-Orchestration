from django.urls import path
from .views import JobSubmitView, JobStatusView

urlpatterns = [
    path("jobs",        JobSubmitView.as_view(),  name="job-submit"),
    path("jobs/<uuid:job_id>", JobStatusView.as_view(), name="job-status"),
]