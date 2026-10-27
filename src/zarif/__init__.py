"""Zarif — talimat dosyalarını anlamı koruyarak sadeleştiren CLI + kütüphane aracı."""

from .api import EditResult, edit_file, edit_text

__version__ = "0.1.0"

__all__ = ["EditResult", "edit_file", "edit_text", "__version__"]
