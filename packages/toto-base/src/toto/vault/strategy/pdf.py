# file_strategies/pdf_strategy.py
import os
from PyPDF2 import PdfReader, PdfWriter
from .base import FileStrategy
from .forms import EncryptPdfForm, DecryptPdfForm


class PdfStrategy(FileStrategy):
    def encrypt(self, file_instance, password: str, owner_password: str = None):
        user_password = password
        if not owner_password:
            owner_password = user_password

        input_path = file_instance.file.path
        output_path = os.path.splitext(input_path)[0] + "_encrypted.pdf"

        reader = PdfReader(input_path)
        writer = PdfWriter()

        for page in reader.pages:
            writer.add_page(page)

        writer.encrypt(user_password=user_password, owner_password=owner_password, use_128bit=True)

        with open(output_path, "wb") as f:
            writer.write(f)

        file_instance.file.save(os.path.basename(output_path), open(output_path, "rb"))
        os.remove(input_path)
        file_instance.is_encrypted = True
        file_instance.save()

    def decrypt(self, file_instance, password: str):
        input_path = file_instance.file.path
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}_decrypted{ext}"

        reader = PdfReader(input_path)

        if not reader.is_encrypted:
            raise ValueError("PDF file is not encrypted.")

        try:
            reader.decrypt(password)
        except Exception as e:
            raise ValueError(f"Failed to decrypt PDF: {str(e)}")

        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)

        with open(output_path, "wb") as f:
            writer.write(f)

        file_instance.file.save(os.path.basename(output_path), open(output_path, "rb"))
        os.remove(input_path)
        file_instance.is_encrypted = False
        file_instance.save()

    def decrypt_to_bytes(self, file_instance, password: str) -> tuple:
        from io import BytesIO
        reader = PdfReader(file_instance.file.path)
        if not reader.is_encrypted:
            raise ValueError("PDF is not encrypted.")
        result = reader.decrypt(password)
        if result == 0:
            raise ValueError("Incorrect password.")
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        buf = BytesIO()
        writer.write(buf)
        return buf.getvalue(), "application/pdf"

    def parse_encrypt_form(self, form):
        user_password = form.cleaned_data['user_password']
        owner_password = form.cleaned_data['owner_password'] or user_password
        return {
            'password': user_password,
            'owner_password': owner_password
        }

    def parse_decrypt_form(self, form):
        return {
            'password': form.cleaned_data['password']
        }

    def get_encrypt_template(self):
        return 'admin/encrypt_pdf.html'

    def get_decrypt_template(self):
        return 'admin/decrypt_pdf.html'

    def get_encrypt_form(self, request, ids):
        if request.method == 'POST':
            return EncryptPdfForm(request.POST)
        return EncryptPdfForm(initial={'_selected_action': ids})

    def get_decrypt_form(self, request, ids):
        if request.method == 'POST':
            return DecryptPdfForm(request.POST)
        return DecryptPdfForm(initial={'_selected_action': ids})
