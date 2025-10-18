# projects/tasks.py
from celery import shared_task
from .models import TexFile

@shared_task
def compile_texfile_task(texfile_id):
    try:
        texfile = TexFile.objects.get(id=texfile_id)
        pdf_data = texfile.compile()
        # Optional: save or process the PDF data here
        return f"Compilation successful for {texfile.filename} (size: {len(pdf_data)} bytes)"
    except TexFile.DoesNotExist:
        return f"TexFile with ID {texfile_id} does not exist."
    except Exception as e:
        return f"Compilation failed: {str(e)}"
