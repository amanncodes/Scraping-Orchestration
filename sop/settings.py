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

REDIS_URL           = env("REDIS_URL", default="redis://redis:6379/0")
CELERY_BROKER_URL   = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL

MONGO_URL = env("MONGO_URL", default="mongodb://mongo:27017")
MONGO_DB  = env("MONGO_DB",  default="sop")

SCRAPER_URL     = env("SCRAPER_URL")
SOP_WEBHOOK_URL = env("SOP_WEBHOOK_URL")

CACHE_TTL = {
    "instagram": env.int("CACHE_TTL_INSTAGRAM", default=86400),
}

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES":   ["rest_framework.parsers.JSONParser"],
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"