#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Skrivist UI Action - Adds "Send to Skrivist" button to Calibre toolbar
"""

import os
import json
import uuid
import threading
import urllib.request
import urllib.error
from functools import partial

from calibre.gui2.actions import InterfaceAction
from calibre.gui2 import error_dialog, info_dialog, question_dialog
from calibre.gui2.threaded_jobs import ThreadedJob
from calibre.utils.config import JSONConfig

from qt.core import QMenu, QToolButton

GITHUB_RELEASES_URL = 'https://api.github.com/repos/c0ze/skrivist.tools/releases/latest'
RELEASES_PAGE = 'https://github.com/c0ze/skrivist.tools/releases/latest'

# Plugin configuration stored in calibre config directory
prefs = JSONConfig('plugins/skrivist')
prefs.defaults['api_key'] = ''
prefs.defaults['server_url'] = 'https://api.skriv.ist'


def upload_book(file_path, metadata, api_key, server_url):
    """Upload a single book file to Skrivist, streaming it from disk"""
    upload_url = f'{server_url}/v1/upload'

    # Build multipart request with random boundary
    boundary = f'----SkrivistBoundary{uuid.uuid4().hex}'

    pre = []

    # Add metadata fields
    for key, value in metadata.items():
        pre.append(f'--{boundary}'.encode())
        pre.append(f'Content-Disposition: form-data; name="{key}"'.encode())
        pre.append(b'')
        pre.append(value.encode('utf-8'))

    # File part header — the file bytes themselves are streamed below
    pre.append(f'--{boundary}'.encode())
    pre.append(f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(file_path)}"'.encode())
    pre.append(b'Content-Type: application/epub+zip')
    pre.append(b'')
    pre_bytes = b'\r\n'.join(pre) + b'\r\n'
    post_bytes = f'\r\n--{boundary}--'.encode()

    content_length = len(pre_bytes) + os.path.getsize(file_path) + len(post_bytes)

    def body():
        yield pre_bytes
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                yield chunk
        yield post_bytes

    # Create request. Content-Length must be set explicitly so urllib
    # accepts an iterable body without chunked transfer encoding.
    req = urllib.request.Request(upload_url, data=body())
    req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')
    req.add_header('Content-Length', str(content_length))
    req.add_header('X-API-Key', api_key)

    # Send request
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.loads(response.read().decode('utf-8'))
            if not result.get('success'):
                raise ValueError(result.get('error', 'Upload failed'))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='replace')
        raise ValueError(f'Server error {e.code}: {error_body}')


def upload_books(payloads, api_key, server_url, abort=None, log=None, notifications=None):
    """
    Upload prepared (file_path, metadata) payloads to Skrivist.
    Runs in a ThreadedJob worker thread — no GUI or db access here.
    Returns (success_count, failures) where failures is a list of
    (title, error message) tuples.
    """
    success_count = 0
    failures = []

    for i, (file_path, metadata) in enumerate(payloads):
        if abort is not None and abort.is_set():
            break
        title = metadata.get('title', os.path.basename(file_path))
        try:
            upload_book(file_path, metadata, api_key, server_url)
            success_count += 1
        except Exception as e:
            failures.append((title, str(e)))
            if log is not None:
                log.error(f'Failed to upload {title}: {e}')
        if notifications is not None:
            notifications.put(((i + 1) / len(payloads), f'Uploaded {i + 1} of {len(payloads)}'))

    return success_count, failures


class SkrivistAction(InterfaceAction):
    """
    Interface action that adds a toolbar button for sending books to Skrivist
    """
    name = 'Skrivist'
    action_spec = ('Send to Skrivist', None, 'Send selected book(s) to your Skriv.ist library', 'Ctrl+Shift+K')
    popup_type = QToolButton.ToolButtonPopupMode.InstantPopup
    action_add_menu = True

    def genesis(self):
        """Setup the action and menu"""
        # Try custom icon, fall back to built-in cloud-upload icon
        try:
            icon = get_icons('images/icon.png', 'Skrivist')
        except Exception:
            from calibre.gui2 import get_icons as get_builtin_icons
            icon = get_builtin_icons('cloud-upload.png')

        self.qaction.setIcon(icon)
        self.qaction.triggered.connect(self.send_to_skriv)

        # Create menu
        self.menu = QMenu(self.gui)
        self.qaction.setMenu(self.menu)

        # Add menu items
        self.create_menu_actions()

        # Check for updates silently in the background
        t = threading.Thread(target=self._check_for_update, daemon=True)
        t.start()

    def create_menu_actions(self):
        """Create the dropdown menu actions"""
        self.menu.clear()

        # Send selected books
        send_action = self.menu.addAction('Send selected to Skriv')
        send_action.triggered.connect(self.send_to_skriv)

        self.menu.addSeparator()

        # Configure
        config_action = self.menu.addAction('Configure API Key...')
        config_action.triggered.connect(self.show_configuration)

    def send_to_skriv(self):
        """Send selected books to Skrivist cloud"""
        # Check API key is configured
        api_key = prefs['api_key']
        if not api_key:
            error_dialog(
                self.gui,
                'API Key Required',
                'Please configure your Skrivist API key first.',
                det_msg='Go to Preferences > Plugins > Skrivist to set your API key.',
                show=True
            )
            return

        # Get selected book IDs
        rows = self.gui.library_view.selectionModel().selectedRows()
        if not rows:
            error_dialog(
                self.gui,
                'No Selection',
                'Please select one or more books to send.',
                show=True
            )
            return

        book_ids = list(map(self.gui.library_view.model().id, rows))

        # Confirm with user
        if len(book_ids) > 1:
            if not question_dialog(
                self.gui,
                'Confirm Upload',
                f'Send {len(book_ids)} books to Skriv.ist?'
            ):
                return

        # Only one upload job at a time
        if getattr(self, '_upload_running', False):
            error_dialog(
                self.gui,
                'Upload In Progress',
                'A Skrivist upload job is already running.',
                show=True
            )
            return

        # Resolve file paths and metadata on the UI thread (needs db access),
        # then hand the payloads to a background job for the network work
        db = self.gui.current_db.new_api
        payloads = []
        prep_failures = []
        for book_id in book_ids:
            try:
                payloads.append(self._book_payload(db, book_id))
            except Exception as e:
                title = db.field_for('title', book_id) or f'Book {book_id}'
                prep_failures.append((title, str(e)))

        if not payloads:
            self._show_upload_result(0, prep_failures)
            return

        self._prep_failures = prep_failures
        self._upload_running = True

        server_url = prefs['server_url'].rstrip('/')
        job = ThreadedJob(
            'skrivist_upload',
            f'Sending {len(payloads)} book(s) to Skriv.ist',
            upload_books,
            (payloads, api_key, server_url),
            {},
            self._upload_done
        )
        self.gui.job_manager.run_threaded_job(job)

    def _book_payload(self, db, book_id):
        """Resolve a book to a (file_path, metadata) upload payload"""
        # Get book metadata
        mi = db.get_metadata(book_id, get_cover=False)

        # Get EPUB format (prefer EPUB, fall back to others)
        formats = db.formats(book_id)
        if not formats:
            raise ValueError('Book has no formats')

        # Prefer EPUB
        fmt = 'EPUB' if 'EPUB' in formats else formats[0]
        if fmt != 'EPUB':
            raise ValueError(f'Book is not in EPUB format (has: {formats})')

        # Get file path
        file_path = db.format_abspath(book_id, fmt)
        if not file_path or not os.path.exists(file_path):
            raise ValueError('Could not locate book file')

        # Prepare metadata
        metadata = {
            'title': mi.title or 'Unknown',
            'author': ', '.join(mi.authors) if mi.authors else 'Unknown',
            'language': mi.language if mi.language else 'en',
        }

        return file_path, metadata

    def _upload_done(self, job):
        """Job completion callback — calibre dispatches this on the GUI thread"""
        self._upload_running = False
        prep_failures = getattr(self, '_prep_failures', [])
        self._prep_failures = []

        if job.failed:
            self.gui.job_exception(job, dialog_title='Skrivist Upload Failed')
            return

        success_count, failures = job.result
        self._show_upload_result(success_count, prep_failures + failures)

    def _show_upload_result(self, success_count, failures):
        """Show the final upload result dialog"""
        if not failures:
            info_dialog(
                self.gui,
                'Upload Complete',
                f'Successfully sent {success_count} book(s) to Skriv.ist!',
                show=True
            )
        else:
            details = '\n'.join(f'{title}: {error}' for title, error in failures)
            error_dialog(
                self.gui,
                'Upload Partially Failed',
                f'Sent {success_count} book(s), {len(failures)} failed.',
                det_msg=details,
                show=True
            )

    def _check_for_update(self):
        """
        Silently check GitHub releases for a newer plugin version.
        Runs in a background thread — never blocks the UI.
        Shows a one-time notification bar if a newer version is available.
        """
        try:
            req = urllib.request.Request(
                GITHUB_RELEASES_URL,
                headers={'User-Agent': 'skrivist-calibre-plugin'}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))

            tag = data.get('tag_name', '')  # e.g. "v1.0.4"
            if not tag.startswith('v'):
                return

            # Parse remote version tuple from tag, e.g. "v1.0.4" → (1, 0, 4)
            parts = tag.lstrip('v').split('.')
            remote = tuple(int(x) for x in parts if x.isdigit())

            # Get installed version from plugin metadata
            installed = self.interface_action_base_plugin.version  # tuple e.g. (1, 0, 4)

            if remote > installed:
                # Schedule UI notification on the main thread
                self.gui.job_exception  # ensure gui is alive
                from qt.core import QTimer
                QTimer.singleShot(3000, lambda: self._show_update_notification(tag))

        except Exception:
            # Network errors, timeouts etc. — silently ignored
            pass

    def _show_update_notification(self, new_tag):
        """Show a non-blocking update available notification."""
        from calibre.gui2 import question_dialog
        if question_dialog(
            self.gui,
            'Skrivist Plugin Update Available',
            f'A new version of the Skrivist plugin is available: <b>{new_tag}</b><br><br>'
            f'Download the latest release from GitHub?',
            yes_text='Open Download Page',
            no_text='Later'
        ):
            import webbrowser
            webbrowser.open(RELEASES_PAGE)

    def show_configuration(self):
        """Show the configuration dialog"""
        self.interface_action_base_plugin.do_user_config(self.gui)
