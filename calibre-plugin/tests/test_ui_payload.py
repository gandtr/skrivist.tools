#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Exercise ui.py's _book_payload wiring (size guard + language conversion)
by stubbing the Calibre/Qt runtime so the real ui module imports outside
Calibre. No network, no GUI."""

import os
import sys
import tempfile
import types
import unittest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PLUGIN_DIR)

import retry  # noqa: E402
import upload  # noqa: E402


def _module(name, **attrs):
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[name] = mod
    return mod


class _JSONConfig(dict):
    def __init__(self, _name):
        super().__init__()
        self.defaults = {}

    def __getitem__(self, key):
        return dict.get(self, key, self.defaults.get(key))


class _InterfaceAction(object):
    pass


class _PopupMode(object):
    InstantPopup = 'instant'


class _QToolButton(object):
    ToolButtonPopupMode = _PopupMode


def _install_stubs():
    _module('calibre')
    _module('calibre.customize', InterfaceActionBase=object)
    _module('calibre.gui2', error_dialog=lambda *a, **k: None,
            info_dialog=lambda *a, **k: None, question_dialog=lambda *a, **k: True)
    _module('calibre.gui2.actions', InterfaceAction=_InterfaceAction)
    _module('calibre.gui2.threaded_jobs', ThreadedJob=object)
    _module('calibre.utils')
    _module('calibre.utils.config', JSONConfig=_JSONConfig)
    _module('calibre.utils.localization',
            lang_as_iso639_1=lambda code: {'eng': 'en', 'jpn': 'ja', 'tur': 'tr'}.get(code))
    _module('qt.core', QMenu=object, QTimer=object, QToolButton=_QToolButton,
            QWidget=object, QVBoxLayout=object, QHBoxLayout=object, QLabel=object,
            QLineEdit=object, QPushButton=object, QGroupBox=object)
    _module('qt')
    _module('calibre_plugins')
    _module('calibre_plugins.skrivist')
    sys.modules['calibre_plugins.skrivist.upload'] = upload
    sys.modules['calibre_plugins.skrivist.retry'] = retry
    import config
    sys.modules['calibre_plugins.skrivist.config'] = config


_install_stubs()
import ui  # noqa: E402


class _Metadata(object):
    def __init__(self, title, authors, language):
        self.title = title
        self.authors = authors
        self.language = language


class _FakeDb(object):
    def __init__(self, path, formats=('EPUB',), title='Book', authors=('Ann Author',), language='eng'):
        self._path = path
        self._formats = list(formats)
        self._mi = _Metadata(title, list(authors), language)

    def get_metadata(self, book_id, get_cover=False):
        return self._mi

    def formats(self, book_id):
        return self._formats

    def format_abspath(self, book_id, fmt):
        return self._path


class TestBookPayload(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.epub', delete=False)
        self.tmp.write(b'PK\x03\x04 fake epub')
        self.tmp.close()

    def tearDown(self):
        os.unlink(self.tmp.name)

    def _payload(self, **db_kwargs):
        db = _FakeDb(self.tmp.name, **db_kwargs)
        return ui.SkrivistAction._book_payload(None, db, 1)

    def test_language_is_converted_to_iso639_1(self):
        path, metadata = self._payload(language='jpn')
        self.assertEqual(path, self.tmp.name)
        self.assertEqual(metadata['language'], 'ja')
        self.assertEqual(metadata['title'], 'Book')
        self.assertEqual(metadata['author'], 'Ann Author')

    def test_unknown_language_falls_back_to_en(self):
        _, metadata = self._payload(language='xxx')
        self.assertEqual(metadata['language'], 'en')

    def test_size_guard_rejects_oversized_epub(self):
        with open(self.tmp.name, 'wb') as fh:
            fh.truncate(upload.UPLOAD_SIZE_LIMIT + 1)
        with self.assertRaises(ValueError) as ctx:
            self._payload()
        self.assertIn('50 MB', str(ctx.exception))

    def test_size_guard_accepts_exact_limit(self):
        with open(self.tmp.name, 'wb') as fh:
            fh.truncate(upload.UPLOAD_SIZE_LIMIT)
        _, metadata = self._payload()
        self.assertEqual(metadata['language'], 'en')

    def test_non_epub_format_is_rejected(self):
        with self.assertRaises(ValueError):
            self._payload(formats=('MOBI',))


class TestPrefs(unittest.TestCase):
    def test_ui_reads_the_prefs_the_settings_widget_writes(self):
        # JSONConfig never re-reads its file, so a second instance in ui.py
        # would keep a stale API key until Calibre restarts.
        self.assertIs(ui.prefs, sys.modules['calibre_plugins.skrivist.config'].prefs)


if __name__ == '__main__':
    unittest.main()
