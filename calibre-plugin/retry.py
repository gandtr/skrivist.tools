#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Pure helpers for Skrivist upload errors and rate-limit wait/retry.

Kept free of Calibre imports so unit tests can run with stock Python.
"""

import json

UPLOAD_SIZE_LIMIT = 52428800  # 50 MiB, matches the server multer cap
MAX_WAIT_SECONDS = 900


class UploadHttpError(ValueError):
    """HTTP failure from the Skrivist upload API."""

    def __init__(self, message, status_code=None, code=None, retry_after=0):
        super(UploadHttpError, self).__init__(message)
        self.status_code = status_code
        self.code = code
        self.retry_after = retry_after


def parse_error_body(body_text):
    """Return (error_message, code_or_None) from a server response body."""
    text = body_text if isinstance(body_text, str) else ''
    try:
        data = json.loads(text) if text else None
    except (ValueError, TypeError):
        data = None
    if isinstance(data, dict):
        msg = data.get('error')
        code = data.get('code')
        if isinstance(msg, str) and msg:
            return msg, code
        return text or 'Upload failed', code
    return text or 'Upload failed', None


def _header(headers, name):
    if headers is None:
        return None
    getter = getattr(headers, 'get', None)
    if callable(getter):
        value = getter(name)
        if value is not None:
            return value
    items = getattr(headers, 'items', None)
    if callable(items):
        wanted = name.lower()
        for key, value in items():
            if str(key).lower() == wanted:
                return value
    return None


def retry_after_seconds(headers, cap=MAX_WAIT_SECONDS):
    """Delay in seconds from Retry-After or RateLimit-Reset, capped.

    express-rate-limit standardHeaders reports both as a duration in seconds.
    """
    raw = _header(headers, 'Retry-After')
    if raw is None:
        raw = _header(headers, 'RateLimit-Reset')
    if raw is None:
        return 0
    try:
        seconds = int(float(str(raw).strip()))
    except (ValueError, TypeError):
        return 0
    if seconds < 0:
        return 0
    return min(seconds, cap)


def classify_429(error_code):
    """How to handle HTTP 429: 'quota' (no retry) or 'rate_limit' (wait+retry)."""
    if error_code == 'QUOTA_EXCEEDED':
        return 'quota'
    return 'rate_limit'


def remaining_not_attempted_message(wait_seconds):
    minutes = max(1, (int(wait_seconds) + 59) // 60)
    return (
        'not attempted — server rate limit; try again in {} minutes'.format(
            minutes
        )
    )


def wait_for_retry(seconds, abort=None, sleep=None):
    """Sleep in 1-second steps. Return False if abort is set, else True."""
    if sleep is None:
        import time
        sleep = time.sleep
    steps = max(0, int(seconds))
    for _ in range(steps):
        if abort is not None and abort.is_set():
            return False
        sleep(1)
    if abort is not None and abort.is_set():
        return False
    return True
