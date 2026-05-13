# file_strategies/image_strategy.py
import os
from cryptography.fernet import Fernet
from .base import FileStrategy
from .forms import EncryptFileForm, DecryptFileForm


class ImageStrategy(FileStrategy):

    def encrypt(self, file_instance, password: str, owner_password: str = None):
        input_path = file_instance.file.path
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}_enc{ext}"

        keyring = file_instance.owner.uservaults.first()
        if not keyring:
            raise ValueError("No UserVault associated with user.")

        key = keyring.derive_key(password)
        fernet = Fernet(key)

        with open(input_path, 'rb') as f:
            data = f.read()

        encrypted_data = fernet.encrypt(data)

        with open(output_path, 'wb') as f:
            f.write(encrypted_data)

        file_instance.file.save(os.path.basename(output_path), open(output_path, 'rb'))
        os.remove(input_path)
        file_instance.is_encrypted = True
        file_instance.save()

    def decrypt(self, file_instance, password: str):
        input_path = file_instance.file.path
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}_dec{ext}"

        keyring = file_instance.owner.uservaults.first()
        if not keyring:
            raise ValueError("No UserVault associated with user.")

        key = keyring.derive_key(password)
        fernet = Fernet(key)

        try:
            with open(input_path, 'rb') as f:
                encrypted_data = f.read()

            decrypted_data = fernet.decrypt(encrypted_data)

            with open(output_path, 'wb') as f:
                f.write(decrypted_data)

            file_instance.file.save(os.path.basename(output_path), open(output_path, 'rb'))
            os.remove(input_path)
            file_instance.is_encrypted = False
            file_instance.save()
        except Exception as e:
            raise ValueError(f"Decryption failed: {str(e)}")

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
