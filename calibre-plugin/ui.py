#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Skrivist UI Action - Adds "Send to Skrivist" button to Calibre toolbar
"""

import os
import html
import json
import re
import threading
import urllib.request
from functools import partial

from calibre.gui2.actions import InterfaceAction
from calibre.gui2 import error_dialog, info_dialog, question_dialog
from calibre.gui2.threaded_jobs import ThreadedJob
from calibre.utils.config import JSONConfig
from calibre.utils.localization import lang_as_iso639_1

from qt.core import QMenu, QTimer, QToolButton

from calibre_plugins.skrivist.upload import (
    check_size,
    normalize_language,
    upload_books,
)

GITHUB_RELEASES_URL = 'https://api.github.com/repos/gandtr/skrivist.tools/releases/latest'
RELEASES_PAGE = 'https://github.com/gandtr/skrivist.tools/releases/latest'

# Plugin configuration stored in calibre config directory
prefs = JSONConfig('plugins/skrivist')
prefs.defaults['api_key'] = ''
prefs.defaults['server_url'] = 'https://api.skriv.ist'


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

        # Create the update-notification dispatcher here, on the GUI thread:
        # calibre's Dispatcher is a QObject whose queued call is delivered in
        # the thread that created it, so constructing it inside the worker
        # thread (which has no event loop) would silently drop the
        # notification. self.Dispatcher is partial(Dispatcher, parent=self),
        # installed by InterfaceAction.do_genesis before it calls genesis(); it
        # parents the dispatcher to this action so Qt owns it, instead of
        # leaving a parentless top-level QObject whose only tie to us is a
        # reference cycle for the cyclic GC to find. Still kept on self so it
        # cannot be collected before the queued call is delivered.
        self._update_dispatcher = self.Dispatcher(self._schedule_update_notification)

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

        # ThreadedJob calls the callback on its worker thread, so wrap it in
        # Dispatcher to get back onto the GUI thread. Concurrent launches are
        # serialized by the job type's max_concurrent_count of 1, so prep
        # failures are bound into the callback rather than stored on self.
        server_url = prefs['server_url'].rstrip('/')

        # Warn when the API key would be sent over plaintext HTTP,
        # except for local servers
        from urllib.parse import urlparse
        parsed = urlparse(server_url)
        if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
            if not question_dialog(
                self.gui,
                'Insecure Server URL',
                f'The configured server URL ({html.escape(server_url)}) uses plain HTTP.<br>'
                'Your API key will be sent unencrypted.<br><br>Continue anyway?',
                yes_text='Continue',
                no_text='Cancel'
            ):
                return

        job = ThreadedJob(
            'skrivist_upload',
            f'Sending {len(payloads)} book(s) to Skriv.ist',
            upload_books,
            (payloads, api_key, server_url),
            {},
            self.Dispatcher(partial(self._upload_done, prep_failures))
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

        check_size(os.path.getsize(file_path))

        # Prepare metadata
        metadata = {
            'title': mi.title or 'Unknown',
            'author': ', '.join(mi.authors) if mi.authors else 'Unknown',
            'language': normalize_language(mi.language, lang_as_iso639_1),
        }

        return file_path, metadata

    def _upload_done(self, prep_failures, job):
        """Job completion callback — runs on the GUI thread via Dispatcher"""
        # ThreadedJob is killable and calibre marks a killed job failed, but the
        # worker still invokes this callback afterwards. The user chose to stop,
        # so a failure dialog would only report their own action back at them.
        if getattr(job, 'killed', False):
            return

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
        If a newer version is found, hands the tag to the GUI thread, which
        prompts at most once per release tag (see _show_update_notification).
        """
        try:
            req = urllib.request.Request(
                GITHUB_RELEASES_URL,
                headers={'User-Agent': 'skrivist-calibre-plugin'}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))

            tag = data.get('tag_name', '')  # e.g. "v1.0.4"

            # Only a strict release tag "vMAJOR.MINOR.PATCH" is an update we
            # offer. Scraping digits out of anything else is actively wrong:
            # "v1.0.5rc1" would yield (1, 0, 51) and look like a huge jump,
            # and "v1.0.6-rc1" would be indistinguishable from the real
            # v1.0.6. release.yml publishes on any "v*" tag, so a prerelease
            # can legitimately appear here — decline it rather than guess.
            match = re.fullmatch(r'v(\d+)\.(\d+)\.(\d+)', tag)
            if not match:
                return
            remote = tuple(int(part) for part in match.groups())

            # Get installed version from plugin metadata
            installed = self.interface_action_base_plugin.version  # tuple e.g. (1, 0, 4)

            # Pad the shorter tuple with zeros so v1.0 < v1.0.4 compares correctly
            length = max(len(remote), len(installed))
            remote_padded = remote + (0,) * (length - len(remote))
            installed_padded = tuple(installed) + (0,) * (length - len(installed))

            if remote_padded > installed_padded:
                # Marshal the notification onto the GUI thread via the
                # dispatcher created in genesis() (see there for why it must
                # be constructed on the GUI thread).
                self._update_dispatcher(tag)

        except Exception:
            # Network errors, timeouts etc. — silently ignored
            pass

    def _schedule_update_notification(self, new_tag):
        """
        Runs on the GUI thread via the dispatcher created in genesis().

        genesis() runs during calibre startup, before the main window is shown,
        and calibre's splash path calls QApplication.processEvents(), which
        delivers queued signals. On a fast network the dispatched call can
        therefore arrive while self.gui is still hidden, and parenting a modal
        to an unshown window blocks startup behind an invisible dialog. Give
        the main window a grace period to come up first.

        The delay has to be armed here rather than in the update-check thread:
        a QTimer needs the event loop of the thread that starts it, and that
        worker thread has none.
        """
        QTimer.singleShot(3000, partial(self._show_update_notification, new_tag))

    def _show_update_notification(self, new_tag):
        """
        Ask (modally) whether to open the download page for a newer release.

        Runs on the GUI thread, a few seconds after the check succeeded.
        skip_dialog_name is scoped to the release tag so a dismissal is
        remembered for that release instead of re-prompting on every calibre
        start; a later release prompts again. skip_dialog_skipped_value=False
        so a remembered dismissal means "do nothing" — never "silently open a
        browser at startup".
        """
        try:
            open_download_page = question_dialog(
                self.gui,
                'Skrivist Plugin Update Available',
                f'A new version of the Skrivist plugin is available: <b>{html.escape(new_tag)}</b><br><br>'
                f'Download the latest release from GitHub?',
                yes_text='Open Download Page',
                no_text='Later',
                skip_dialog_name=f'skrivist_plugin_update_{new_tag}',
                skip_dialog_msg='Remind me about this version again',
                skip_dialog_skipped_value=False,
                skip_dialog_skip_precheck=False
            )
        except RuntimeError:
            # This runs outside _check_for_update's try/except. If calibre is
            # shutting down, the Main window's C++ object may already be gone,
            # and parenting a dialog to it raises "wrapped C/C++ object ... has
            # been deleted". There is nothing left to show — drop it quietly.
            return

        if open_download_page:
            import webbrowser
            webbrowser.open(RELEASES_PAGE)

    def show_configuration(self):
        """Show the configuration dialog"""
        self.interface_action_base_plugin.do_user_config(self.gui)
