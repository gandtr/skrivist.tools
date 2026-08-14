#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Skrivist Calibre Plugin
Send books from Calibre to your Skriv.ist cloud library
"""

from calibre.customize import InterfaceActionBase

class SkrivistPlugin(InterfaceActionBase):
    """
    Main plugin class for Skrivist integration
    """
    name = 'Skrivist'
    description = 'Send books to your Skriv.ist cloud library'
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'Skrivist'
    version = (1, 0, 5)
    minimum_calibre_version = (5, 0, 0)

    # The actual plugin UI is implemented in ui.py
    actual_plugin = 'calibre_plugins.skrivist.ui:SkrivistAction'

    def is_customizable(self):
        """Plugin has configuration options"""
        return True

    def config_widget(self):
        """Return configuration widget"""
        from calibre_plugins.skrivist.config import ConfigWidget
        return ConfigWidget()

    def save_settings(self, config_widget):
        """Save configuration from widget"""
        config_widget.save_settings()
