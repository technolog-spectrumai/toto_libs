from celery import shared_task
from .models import TexFile, LatexProject

@shared_task
def compile_project_task(project_id):
    try:
        project = LatexProject.objects.get(id=project_id)
        compiled_files = project.compile_all()
        return f"Compiled {len(compiled_files)} files in project {project.name}"
    except Exception as e:
        return f"Failed to compile project {project_id}: {str(e)}"
