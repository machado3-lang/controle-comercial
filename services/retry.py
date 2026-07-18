"""Retry utilities for external API calls."""
import time
import logging
from functools import wraps

logger = logging.getLogger(__name__)

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

def with_retry(max_retries=3, base_delay=1.0, max_delay=10.0):
    """Decorator that retries a function on transient failures with exponential backoff."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries:
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        logger.warning(f"Retry {attempt + 1}/{max_retries} for {func.__name__}: {e}. Waiting {delay:.1f}s")
                        time.sleep(delay)
            raise last_exception
        return wrapper
    return decorator


def retry_on_transient(func, *args, max_retries=2, **kwargs):
    """Call a function with retry on transient failures. Returns (result, None) or (None, error_str)."""
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return func(*args, **kwargs), None
        except Exception as e:
            last_exc = str(e)
            logger.warning(f"Tentativa {attempt + 1}/{max_retries + 1} falhou: {e}")
            if attempt < max_retries:
                time.sleep(2 ** attempt)
    return None, str(last_exc) if last_exc else "Erro desconhecido"