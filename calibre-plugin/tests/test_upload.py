#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Behavioral tests for Calibre-independent upload helpers (no Calibre import)."""

import io
import os
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from retry import remaining_not_attempted_message  # noqa: E402
from upload import (  # noqa: E402
    AUTH_NOT_ATTEMPTED,
    UPLOAD_SIZE_LIMIT,
    check_size,
    normalize_language,
    upload_book,
    upload_books,
)


class FakeResponse(object):
    def __init__(self, body='{"success": true}'):
        if not isinstance(body, bytes):
            body = body.encode('utf-8')
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class ScriptedUrlOpen(object):
    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        if not self.script:
            raise AssertionError('urlopen called more times than scripted')
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class AbortFlag(object):
    def __init__(self, value=False):
        self.value = value

    def is_set(self):
        return self.value

    def set(self):
        self.value = True


def http_error(code, body, headers=None):
    if not isinstance(body, bytes):
        body = body.encode('utf-8')
    return urllib.error.HTTPError(
        'http://example.test/v1/upload',
        code,
        'Error',
        headers or {},
        io.BytesIO(body),
    )


def through_urlopen(urlopen):
    def _upload(file_path, metadata, api_key, server_url):
        return upload_book(
            file_path, metadata, api_key, server_url, urlopen=urlopen
        )

    return _upload


def fake_iso639_1(raw):
    if raw == 'eng':
        return 'en'
    return None


class TestCheckSize(unittest.TestCase):
    def test_exactly_limit_passes(self):
        check_size(52428800)

    def test_one_byte_over_fails(self):
        with self.assertRaises(ValueError) as ctx:
            check_size(52428801)
        self.assertEqual(
            str(ctx.exception), 'EPUB is larger than the 50 MB upload limit'
        )
        self.assertEqual(UPLOAD_SIZE_LIMIT, 52428800)


class TestNormalizeLanguage(unittest.TestCase):
    def test_eng_becomes_en(self):
        self.assertEqual(normalize_language('eng', fake_iso639_1), 'en')

    def test_none_and_unknown_default_to_en(self):
        self.assertEqual(normalize_language(None, fake_iso639_1), 'en')
        self.assertEqual(normalize_language('zz', fake_iso639_1), 'en')


class UploadTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.dir = self._tmpdir.name

    def tearDown(self):
        self._tmpdir.cleanup()

    def _book(self, name, title):
        path = os.path.join(self.dir, name)
        with open(path, 'wb') as fh:
            fh.write(b'PK\x03\x04fake-epub')
        return path, {'title': title}

    def _run_books(self, payloads, urlopen, abort=None, sleep=None):
        if sleep is None:
            sleep = lambda _seconds: None
        return upload_books(
            payloads,
            'sk_test',
            'http://example.test',
            abort=abort,
            upload=through_urlopen(urlopen),
            sleep=sleep,
        )


class TestUploadBook(UploadTestCase):
    def test_success_json(self):
        path, meta = self._book('a.epub', 'A')
        urlopen = ScriptedUrlOpen([FakeResponse('{"success": true}')])
        upload_book(path, meta, 'sk_test', 'http://example.test', urlopen=urlopen)
        self.assertEqual(len(urlopen.requests), 1)
        req = urlopen.requests[0]
        self.assertTrue(req.full_url.endswith('/v1/upload'))
        self.assertEqual(req.get_header('X-api-key') or req.get_header('X-API-Key'), 'sk_test')

    def test_401_propagates_server_error(self):
        path, meta = self._book('a.epub', 'A')
        urlopen = ScriptedUrlOpen(
            [http_error(401, '{"error":"Invalid API key"}')]
        )
        with self.assertRaises(Exception) as ctx:
            upload_book(
                path, meta, 'sk_test', 'http://example.test', urlopen=urlopen
            )
        self.assertEqual(str(ctx.exception), 'Invalid API key')
        self.assertEqual(ctx.exception.status_code, 401)

    def test_413_propagates_server_error(self):
        path, meta = self._book('a.epub', 'A')
        urlopen = ScriptedUrlOpen(
            [http_error(413, '{"error":"File too large"}')]
        )
        with self.assertRaises(Exception) as ctx:
            upload_book(
                path, meta, 'sk_test', 'http://example.test', urlopen=urlopen
            )
        self.assertEqual(str(ctx.exception), 'File too large')
        self.assertEqual(ctx.exception.status_code, 413)


class TestUploadBooks(UploadTestCase):
    def test_quota_fails_that_book_only_and_continues(self):
        a = self._book('a.epub', 'A')
        b = self._book('b.epub', 'B')
        urlopen = ScriptedUrlOpen(
            [
                http_error(
                    429,
                    '{"error":"Cloud library full.","code":"QUOTA_EXCEEDED"}',
                ),
                FakeResponse('{"success": true}'),
            ]
        )
        slept = []
        success, failures = self._run_books(
            [a, b], urlopen, sleep=slept.append
        )
        self.assertEqual(success, 1)
        self.assertEqual(failures, [('A', 'Cloud library full.')])
        self.assertEqual(slept, [])
        self.assertEqual(len(urlopen.requests), 2)

    def test_rate_limit_waits_then_retries_once_and_succeeds(self):
        a = self._book('a.epub', 'A')
        urlopen = ScriptedUrlOpen(
            [
                http_error(
                    429,
                    '{"error":"Too many requests"}',
                    {'Retry-After': '2'},
                ),
                FakeResponse('{"success": true}'),
            ]
        )
        slept = []
        success, failures = self._run_books([a], urlopen, sleep=slept.append)
        self.assertEqual(success, 1)
        self.assertEqual(failures, [])
        self.assertEqual(slept, [1, 1])
        self.assertEqual(len(urlopen.requests), 2)

    def test_auth_failure_stops_batch(self):
        a = self._book('a.epub', 'A')
        b = self._book('b.epub', 'B')
        c = self._book('c.epub', 'C')
        urlopen = ScriptedUrlOpen(
            [http_error(403, '{"error":"Subscription required","code":"NOT_SUBSCRIBED"}')]
        )
        success, failures = self._run_books([a, b, c], urlopen)
        self.assertEqual(success, 0)
        self.assertEqual(
            failures,
            [
                ('A', 'Subscription required'),
                ('B', AUTH_NOT_ATTEMPTED),
                ('C', AUTH_NOT_ATTEMPTED),
            ],
        )
        self.assertEqual(len(urlopen.requests), 1)

    def test_auth_failure_on_rate_limit_retry_stops_batch(self):
        a = self._book('a.epub', 'A')
        b = self._book('b.epub', 'B')
        urlopen = ScriptedUrlOpen(
            [
                http_error(429, '{"error":"Too many requests"}', {'Retry-After': '1'}),
                http_error(401, '{"error":"Invalid API key"}'),
            ]
        )
        success, failures = self._run_books([a, b], urlopen, sleep=lambda _s: None)
        self.assertEqual(success, 0)
        self.assertEqual(
            failures,
            [('A', 'Invalid API key'), ('B', AUTH_NOT_ATTEMPTED)],
        )
        self.assertEqual(len(urlopen.requests), 2)

    def test_second_rate_limit_stops_batch(self):
        a = self._book('a.epub', 'A')
        b = self._book('b.epub', 'B')
        c = self._book('c.epub', 'C')
        urlopen = ScriptedUrlOpen(
            [
                http_error(
                    429,
                    '{"error":"Too many requests"}',
                    {'Retry-After': '1'},
                ),
                http_error(
                    429,
                    '{"error":"Still limited"}',
                    {'Retry-After': '900'},
                ),
            ]
        )
        slept = []
        success, failures = self._run_books(
            [a, b, c], urlopen, sleep=slept.append
        )
        msg = remaining_not_attempted_message(900)
        self.assertEqual(success, 0)
        self.assertEqual(
            failures,
            [
                ('A', 'Still limited'),
                ('B', msg),
                ('C', msg),
            ],
        )
        self.assertEqual(msg, 'not attempted — server rate limit; try again in 15 minutes')
        self.assertEqual(len(urlopen.requests), 2)

    def test_quota_on_retry_fails_book_and_continues(self):
        a = self._book('a.epub', 'A')
        b = self._book('b.epub', 'B')
        urlopen = ScriptedUrlOpen(
            [
                http_error(
                    429,
                    '{"error":"Too many requests"}',
                    {'Retry-After': '1'},
                ),
                http_error(
                    429,
                    '{"error":"Cloud library full.","code":"QUOTA_EXCEEDED"}',
                ),
                FakeResponse('{"success": true}'),
            ]
        )
        success, failures = self._run_books([a, b], urlopen)
        self.assertEqual(success, 1)
        self.assertEqual(failures, [('A', 'Cloud library full.')])
        self.assertEqual(len(urlopen.requests), 3)

    def test_abort_during_wait_uploads_nothing_further(self):
        a = self._book('a.epub', 'A')
        b = self._book('b.epub', 'B')
        abort = AbortFlag(False)
        urlopen = ScriptedUrlOpen(
            [
                http_error(
                    429,
                    '{"error":"Too many requests"}',
                    {'Retry-After': '1'},
                ),
            ]
        )

        def sleep(_seconds):
            abort.set()

        success, failures = self._run_books(
            [a, b], urlopen, abort=abort, sleep=sleep
        )
        self.assertEqual(success, 0)
        self.assertEqual(failures, [])
        self.assertEqual(len(urlopen.requests), 1)

    def test_abort_before_retry_after_zero_wait(self):
        a = self._book('a.epub', 'A')
        b = self._book('b.epub', 'B')
        answers = [False, False, True]

        class SequenceAbort(object):
            def is_set(self):
                if not answers:
                    return True
                return answers.pop(0)

        urlopen = ScriptedUrlOpen(
            [
                http_error(
                    429,
                    '{"error":"Too many requests"}',
                    {'Retry-After': '0'},
                ),
            ]
        )
        success, failures = self._run_books(
            [a, b], urlopen, abort=SequenceAbort()
        )
        self.assertEqual(success, 0)
        self.assertEqual(failures, [])
        self.assertEqual(len(urlopen.requests), 1)


if __name__ == '__main__':
    unittest.main()
