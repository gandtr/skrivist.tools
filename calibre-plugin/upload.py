#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Calibre-free upload helpers for the Skrivist plugin.

Kept free of Calibre/Qt imports so unit tests can run with stock Python.
"""

import json
import os
import time
import uuid
import urllib.error
import urllib.request

try:
    from calibre_plugins.skrivist.retry import (
        UPLOAD_SIZE_LIMIT,
        UploadHttpError,
        classify_429,
        parse_error_body,
        remaining_not_attempted_message,
        retry_after_seconds,
        wait_for_retry,
    )
except ImportError:
    from retry import (  # type: ignore
        UPLOAD_SIZE_LIMIT,
        UploadHttpError,
        classify_429,
        parse_error_body,
        remaining_not_attempted_message,
        retry_after_seconds,
        wait_for_retry,
    )


def check_size(path_size):
    """Raise ValueError if the EPUB is over the 50 MB upload limit."""
    if path_size > UPLOAD_SIZE_LIMIT:
        raise ValueError('EPUB is larger than the 50 MB upload limit')


def normalize_language(raw, to_iso639_1):
    """Map a language tag to ISO 639-1 via an injected converter; default 'en'."""
    converted = to_iso639_1(raw) if to_iso639_1 is not None else None
    return converted or 'en'


def build_multipart(file_path, metadata, boundary=None):
    """Build multipart header/footer for an EPUB upload.

    Returns (content_type, header_bytes, footer_bytes, content_length).
    """
    if boundary is None:
        boundary = '----SkrivistBoundary{}'.format(uuid.uuid4().hex)

    pre = []
    for key, value in metadata.items():
        pre.append('--{}'.format(boundary).encode())
        pre.append('Content-Disposition: form-data; name="{}"'.format(key).encode())
        pre.append(b'')
        pre.append(value.encode('utf-8'))

    pre.append('--{}'.format(boundary).encode())
    pre.append(
        'Content-Disposition: form-data; name="file"; filename="{}"'.format(
            os.path.basename(file_path)
        ).encode()
    )
    pre.append(b'Content-Type: application/epub+zip')
    pre.append(b'')
    pre_bytes = b'\r\n'.join(pre) + b'\r\n'
    post_bytes = '\r\n--{}--'.format(boundary).encode()
    content_length = len(pre_bytes) + os.path.getsize(file_path) + len(post_bytes)
    content_type = 'multipart/form-data; boundary={}'.format(boundary)
    return content_type, pre_bytes, post_bytes, content_length


def upload_book(file_path, metadata, api_key, server_url, *, urlopen=urllib.request.urlopen):
    """Upload a single book file to Skrivist, streaming it from disk."""
    upload_url = '{}/v1/upload'.format(server_url)
    content_type, pre_bytes, post_bytes, content_length = build_multipart(
        file_path, metadata
    )

    def body():
        yield pre_bytes
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                yield chunk
        yield post_bytes

    req = urllib.request.Request(upload_url, data=body())
    req.add_header('Content-Type', content_type)
    req.add_header('Content-Length', str(content_length))
    req.add_header('X-API-Key', api_key)

    try:
        with urlopen(req, timeout=300) as response:
            result = json.loads(response.read().decode('utf-8'))
            if not result.get('success'):
                raise ValueError(result.get('error', 'Upload failed'))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='replace')
        message, code = parse_error_body(error_body)
        raise UploadHttpError(
            message,
            status_code=e.code,
            code=code,
            retry_after=retry_after_seconds(e.headers),
        )


AUTH_NOT_ATTEMPTED = (
    'not attempted — check your API key and Skrivist Cloud subscription'
)


def _skip_rest(payloads, start, message, failures):
    for rest_path, rest_meta in payloads[start:]:
        failures.append(
            (rest_meta.get('title', os.path.basename(rest_path)), message)
        )


def _aborted(abort):
    return abort is not None and abort.is_set()


def upload_books(
    payloads,
    api_key,
    server_url,
    abort=None,
    log=None,
    notifications=None,
    *,
    upload=upload_book,
    sleep=time.sleep,
):
    """
    Upload prepared (file_path, metadata) payloads to Skrivist.
    Runs in a ThreadedJob worker thread — no GUI or db access here.
    Returns (success_count, failures) where failures is a list of
    (title, error message) tuples.
    """
    success_count = 0
    failures = []
    total = len(payloads)

    def report(progress, message):
        if notifications is not None:
            notifications.put((progress, message))

    i = 0
    while i < total:
        if _aborted(abort):
            break
        file_path, metadata = payloads[i]
        title = metadata.get('title', os.path.basename(file_path))
        try:
            upload(file_path, metadata, api_key, server_url)
            success_count += 1
        except UploadHttpError as e:
            if e.status_code == 429 and classify_429(e.code) == 'quota':
                failures.append((title, str(e)))
                if log is not None:
                    log.error('Failed to upload {}: {}'.format(title, e))
            elif e.status_code == 429:
                wait_s = e.retry_after
                report(
                    (i / total) if total else 1,
                    'Server rate limit; waiting {}s'.format(wait_s),
                )
                if not wait_for_retry(wait_s, abort=abort, sleep=sleep):
                    break
                if _aborted(abort):
                    break
                try:
                    upload(file_path, metadata, api_key, server_url)
                    success_count += 1
                except UploadHttpError as e2:
                    if e2.status_code == 429 and classify_429(e2.code) == 'quota':
                        failures.append((title, str(e2)))
                        if log is not None:
                            log.error('Failed to upload {}: {}'.format(title, e2))
                    elif e2.status_code == 429:
                        msg = remaining_not_attempted_message(e2.retry_after)
                        failures.append((title, str(e2)))
                        if log is not None:
                            log.error('Failed to upload {}: {}'.format(title, e2))
                        _skip_rest(payloads, i + 1, msg, failures)
                        report(1, msg)
                        break
                    else:
                        failures.append((title, str(e2)))
                        if log is not None:
                            log.error('Failed to upload {}: {}'.format(title, e2))
                except Exception as e2:
                    failures.append((title, str(e2)))
                    if log is not None:
                        log.error('Failed to upload {}: {}'.format(title, e2))
            else:
                failures.append((title, str(e)))
                if log is not None:
                    log.error('Failed to upload {}: {}'.format(title, e))
                if e.status_code in (401, 403):
                    # A bad key or a lapsed subscription fails every book
                    # the same way; don't stream the rest of the batch.
                    _skip_rest(payloads, i + 1, AUTH_NOT_ATTEMPTED, failures)
                    report(1, AUTH_NOT_ATTEMPTED)
                    break
        except Exception as e:
            failures.append((title, str(e)))
            if log is not None:
                log.error('Failed to upload {}: {}'.format(title, e))
        report((i + 1) / total if total else 1, 'Uploaded {} of {}'.format(i + 1, total))
        i += 1

    return success_count, failures
