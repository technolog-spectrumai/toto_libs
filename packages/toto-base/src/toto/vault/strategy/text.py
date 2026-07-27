import os
import tempfile
from cryptography.fernet import Fernet
from .base import FileStrategy
from .forms import EncryptFileForm, DecryptFileForm


class TextStrategy(FileStrategy):

    def encrypt(self, file_instance, password: str, owner_password: str = None):
        input_path = file_instance.file.path
        _, ext = os.path.splitext(input_path)

        keyring = file_instance.owner.user_strongboxes.first()
        if not keyring:
            raise ValueError("No UserVault associated with user.")

        key = keyring.derive_key(password)
        fernet = Fernet(key)

        with open(input_path, 'rb') as f:
            data = f.read()

        encrypted_data = fernet.encrypt(data)

        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(encrypted_data)
            tmp_path = tmp.name

        try:
            with open(tmp_path, 'rb') as f:
                file_instance.file.save(os.path.basename(input_path), f, save=False)
            os.remove(input_path)
            file_instance.is_encrypted = True
            file_instance.save()
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def decrypt(self, file_instance, password: str):
        input_path = file_instance.file.path
        _, ext = os.path.splitext(input_path)

        keyring = file_instance.owner.user_strongboxes.first()
        if not keyring:
            raise ValueError("No UserVault associated with user.")

        key = keyring.derive_key(password)
        fernet = Fernet(key)

        with open(input_path, 'rb') as f:
            encrypted_data = f.read()

        try:
            decrypted_data = fernet.decrypt(encrypted_data)
        except Exception as e:
            raise ValueError(f"Decryption failed: {str(e)}")

        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(decrypted_data)
            tmp_path = tmp.name

        try:
            with open(tmp_path, 'rb') as f:
                file_instance.file.save(os.path.basename(input_path), f, save=False)
            os.remove(input_path)
            file_instance.is_encrypted = False
            file_instance.save()
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def decrypt_to_bytes(self, file_instance, password: str) -> tuple:
        import mimetypes
        keyring = file_instance.owner.user_strongboxes.first()
        if not keyring:
            raise ValueError("No strongbox associated with user.")
        key = keyring.derive_key(password)
        fernet = Fernet(key)
        with open(file_instance.file.path, "rb") as f:
            encrypted_data = f.read()
        try:
            decrypted = fernet.decrypt(encrypted_data)
        except Exception:
            raise ValueError("Incorrect password.")
        mime, _ = mimetypes.guess_type(file_instance.file.name)
        return decrypted, mime or "application/octet-stream"

    def parse_encrypt_form(self, form):
        password = form.cleaned_data['password']
        return {
            'password': password,
            'owner_password': password
        }

    def parse_decrypt_form(self, form):
        return {
            'password': form.cleaned_data['password']
        }

    def get_encrypt_form(self, request, ids):
        if request.method == 'POST':
            return EncryptFileForm(request.POST)
        return EncryptFileForm(initial={'_selected_action': ids})

    def get_decrypt_form(self, request, ids):
        if request.method == 'POST':
            return DecryptFileForm(request.POST)
        return DecryptFileForm(initial={'_selected_action': ids})
