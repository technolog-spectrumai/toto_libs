from celery import shared_task
from .models import TexFile, LatexProject

# @shared_task
# def compile_texfile_task(texfile_id):
#     try:
#         texfile = TexFile.objects.get(id=texfile_id)
#         texfile.compile()
#         return f"Compiled {texfile.filename}"
#     except Exception as e:
#         return f"Failed to compile TexFile {texfile_id}: {str(e)}"

@shared_task
def compile_project_task(project_id):
    try:
        project = LatexProject.objects.get(id=project_id)
        compiled_files = project.compile_all()
        return f"Compiled {len(compiled_files)} files in project {project.name}"
    except Exception as e:
        return f"Failed to compile project {project_id}: {str(e)}"
