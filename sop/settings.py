import environ
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    "jobs",
    "webhook",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "sop.urls"

DATABASES = {
    "default": env.db("DATABASE_URL")
}

# Redis
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")

# Celery — connects to EXISTING shared Redis broker
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_QUEUES_DEFAULT = "sop.scrape"

# MongoDB
MONGO_URL = env("MONGO_URL", default="mongodb://localhost:27017")
MONGO_DB = env("MONGO_DB", default="sop")

# External services
EXISTING_BACKEND_URL = env("EXISTING_BACKEND_URL")
SOP_WEBHOOK_URL = env("SOP_WEBHOOK_URL")

# Cache TTLs
CACHE_TTL = {
    "instagram": env.int("CACHE_TTL_INSTAGRAM", default=86400),
}

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"