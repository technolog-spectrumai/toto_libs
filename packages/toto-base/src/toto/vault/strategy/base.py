# file_strategies/base.py
from abc import ABC, abstractmethod

class FileStrategy(ABC):
    @abstractmethod
    def encrypt(self, file_instance, password: str, owner_password: str = None):
        pass

    @abstractmethod
    def decrypt(self, file_instance, password: str):
        pass

    def get_encrypt_context(self, queryset, form):
        return {
            'form': form,
            'queryset': queryset,
            'title': 'Encrypt selected files',
        }

    def get_decrypt_context(self, queryset, form):
        return {
            'form': form,
            'queryset': queryset,
            'title': 'Decrypt selected files',
        }

    @abstractmethod
    def parse_encrypt_form(self, form):
        """Return a dict with normalized password data for encryption"""
        pass

    @abstractmethod
    def parse_decrypt_form(self, form):
        """Return a dict with normalized password data for decryption"""
        pass

    def get_encrypt_template(self):
        return 'admin/encrypt_file.html'

    def get_decrypt_template(self):
        return 'admin/decrypt_file.html'

    @abstractmethod
    def decrypt_to_bytes(self, file_instance, password: str) -> tuple:
        """Decrypt file in-memory without saving. Returns (bytes, content_type)."""
        pass

    @abstractmethod
    def get_encrypt_form(self, request, ids):
        pass

    @abstractmethod
    def get_decrypt_form(self, request, ids):
        pass
