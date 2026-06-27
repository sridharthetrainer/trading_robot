# utils.py

from __future__ import annotations

import time
from functools import wraps
from typing import Callable, TypeVar, Any

F = TypeVar("F", bound=Callable[..., Any])


def retry(max_retries: int = 3, base_delay: float = 1.0):
    """
    Retry decorator with simple linear backoff.

    Example:
        @retry(max_retries=3, base_delay=1)
        def fetch():
            ...
    """
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_err = None
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_err = e
                    if attempt >= max_retries:
                        raise
                    time.sleep(base_delay * attempt)
            raise last_err
        return wrapper  # type: ignore
    return decorator
