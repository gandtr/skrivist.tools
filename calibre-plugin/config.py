#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Skrivist Configuration Widget - Settings UI for API key and server URL
"""

from qt.core import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QGroupBox

from calibre.utils.config import JSONConfig

# Use same prefs as ui.py
prefs = JSONConfig('plugins/skrivist')
prefs.defaults['api_key'] = ''
prefs.defaults['server_url'] = 'https://api.skriv.ist'


class ConfigWidget(QWidget):
    """
    Configuration widget for Skrivist plugin settings
    """

    def __init__(self):
        super().__init__()
        self.setup_ui()
        self.load_settings()

    def setup_ui(self):
        """Build the configuration UI"""
        layout = QVBoxLayout()
        self.setLayout(layout)

        # API Key Section
        api_group = QGroupBox('Skrivist API Key')
        api_layout = QVBoxLayout()
        api_group.setLayout(api_layout)

        # Instructions
        instructions = QLabel(
            'Enter your Skrivist API key to enable uploads.\n'
            'You can generate an API key from your account settings at '
            'app.skriv.ist (Settings → Calibre integration).'
        )
        instructions.setWordWrap(True)
        api_layout.addWidget(instructions)

        # API Key input
        key_layout = QHBoxLayout()
        key_label = QLabel('API Key:')
        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText('sk_xxxxxxxxxxxxxxxx')
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        key_layout.addWidget(key_label)
        key_layout.addWidget(self.api_key_input)

        # Show/hide button
        self.show_key_btn = QPushButton('Show')
        self.show_key_btn.setCheckable(True)
        self.show_key_btn.toggled.connect(self.toggle_key_visibility)
        key_layout.addWidget(self.show_key_btn)

        api_layout.addLayout(key_layout)
        layout.addWidget(api_group)

        # Server URL Section (Advanced)
        server_group = QGroupBox('Server Settings (Advanced)')
        server_layout = QVBoxLayout()
        server_group.setLayout(server_layout)

        server_label = QLabel('Server URL:')
        self.server_url_input = QLineEdit()
        self.server_url_input.setPlaceholderText('https://api.skriv.ist')

        server_layout.addWidget(server_label)
        server_layout.addWidget(self.server_url_input)

        note = QLabel('Only change this if you are running a self-hosted instance.')
        note.setStyleSheet('color: gray; font-size: 10px;')
        server_layout.addWidget(note)

        layout.addWidget(server_group)

        # Stretch to push everything to top
        layout.addStretch()

    def toggle_key_visibility(self, checked):
        """Toggle API key visibility"""
        if checked:
            self.api_key_input.setEchoMode(QLineEdit.EchoMode.Normal)
            self.show_key_btn.setText('Hide')
        else:
            self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
            self.show_key_btn.setText('Show')

    def load_settings(self):
        """Load saved settings into the form"""
        self.api_key_input.setText(prefs['api_key'])
        self.server_url_input.setText(prefs['server_url'])

    def save_settings(self):
        """Save settings from the form"""
        prefs['api_key'] = self.api_key_input.text().strip()
        prefs['server_url'] = self.server_url_input.text().strip() or 'https://api.skriv.ist'
