import structlog
import logging

logging.basicConfig(
    format="%(message)s",
    level=logging.INFO,
)

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.BoundLogger,
    logger_factory=structlog.PrintLoggerFactory(),
)

log = structlog.get_logger()


def log_event(event: str, job_id: str, platform: str, **kwargs):
    log.info(event, job_id=str(job_id), platform=platform, **kwargs)


def log_error(event: str, job_id: str, platform: str, error: str, **kwargs):
    log.error(event, job_id=str(job_id), platform=platform, error=error, **kwargs)