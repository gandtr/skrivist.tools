#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Unit tests for upload retry helpers (no Calibre import)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from retry import (  # noqa: E402
    MAX_WAIT_SECONDS,
    UPLOAD_SIZE_LIMIT,
    classify_429,
    parse_error_body,
    remaining_not_attempted_message,
    retry_after_seconds,
    wait_for_retry,
)


class AbortFlag(object):
    def __init__(self, after=None):
        self.after = after
        self.calls = 0

    def is_set(self):
        self.calls += 1
        if self.after is None:
            return False
        return self.calls > self.after


class _Flag(object):
    def __init__(self, value=False):
        self.value = value

    def is_set(self):
        return self.value


class TestParseErrorBody(unittest.TestCase):
    def test_json_error_and_code(self):
        msg, code = parse_error_body(
            '{"error":"Cloud library full.","code":"QUOTA_EXCEEDED"}'
        )
        self.assertEqual(msg, 'Cloud library full.')
        self.assertEqual(code, 'QUOTA_EXCEEDED')

    def test_raw_body_fallback(self):
        msg, code = parse_error_body('plain failure')
        self.assertEqual(msg, 'plain failure')
        self.assertIsNone(code)

    def test_json_without_error_falls_back_to_raw(self):
        raw = '{"foo":1}'
        msg, code = parse_error_body(raw)
        self.assertEqual(msg, raw)
        self.assertIsNone(code)

    def test_empty_body(self):
        msg, code = parse_error_body('')
        self.assertEqual(msg, 'Upload failed')
        self.assertIsNone(code)


class TestRetryAfterSeconds(unittest.TestCase):
    def test_prefers_retry_after(self):
        self.assertEqual(
            retry_after_seconds({'Retry-After': '12', 'RateLimit-Reset': '99'}),
            12,
        )

    def test_falls_back_to_ratelimit_reset(self):
        self.assertEqual(retry_after_seconds({'RateLimit-Reset': '30'}), 30)

    def test_case_insensitive_dict_keys(self):
        self.assertEqual(retry_after_seconds({'retry-after': '8'}), 8)

    def test_caps_at_900(self):
        self.assertEqual(retry_after_seconds({'Retry-After': '9999'}), MAX_WAIT_SECONDS)

    def test_missing_headers(self):
        self.assertEqual(retry_after_seconds(None), 0)
        self.assertEqual(retry_after_seconds({}), 0)

    def test_invalid_value(self):
        self.assertEqual(retry_after_seconds({'Retry-After': 'soon'}), 0)


class TestClassify429(unittest.TestCase):
    def test_quota_does_not_retry(self):
        self.assertEqual(classify_429('QUOTA_EXCEEDED'), 'quota')

    def test_other_429_is_rate_limit(self):
        self.assertEqual(classify_429(None), 'rate_limit')
        self.assertEqual(classify_429('TOO_MANY'), 'rate_limit')


class TestWaitForRetry(unittest.TestCase):
    def test_sleeps_one_second_steps(self):
        slept = []
        ok = wait_for_retry(3, abort=None, sleep=slept.append)
        self.assertTrue(ok)
        self.assertEqual(slept, [1, 1, 1])

    def test_zero_seconds_does_not_sleep(self):
        slept = []
        ok = wait_for_retry(0, abort=None, sleep=slept.append)
        self.assertTrue(ok)
        self.assertEqual(slept, [])

    def test_abort_stops_wait(self):
        slept = []
        ok = wait_for_retry(10, abort=AbortFlag(after=3), sleep=slept.append)
        self.assertFalse(ok)
        self.assertEqual(slept, [1, 1, 1])

    def test_abort_after_last_sleep_returns_false(self):
        abort = _Flag(False)

        def sleep(_seconds):
            abort.value = True

        ok = wait_for_retry(1, abort=abort, sleep=sleep)
        self.assertFalse(ok)

    def test_zero_seconds_checks_abort(self):
        slept = []
        ok = wait_for_retry(0, abort=_Flag(True), sleep=slept.append)
        self.assertFalse(ok)
        self.assertEqual(slept, [])


class TestRemainingMessage(unittest.TestCase):
    def test_ceils_to_minutes(self):
        self.assertEqual(
            remaining_not_attempted_message(900),
            'not attempted — server rate limit; try again in 15 minutes',
        )
        self.assertEqual(
            remaining_not_attempted_message(1),
            'not attempted — server rate limit; try again in 1 minutes',
        )


class TestSizeLimit(unittest.TestCase):
    def test_matches_50_mib(self):
        self.assertEqual(UPLOAD_SIZE_LIMIT, 50 * 1024 * 1024)


if __name__ == '__main__':
    unittest.main()
