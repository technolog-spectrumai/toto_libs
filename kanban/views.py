from django.views.generic import DetailView, ListView
from django.db.models import Q
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.mixins import LoginRequiredMixin
from kanban.models import Project, Column, Task, Sprint
from oya.page import PageProcessor


class ProjectDetailView(LoginRequiredMixin, DetailView):
    model = Project
    template_name = 'kanban/board.html'
    context_object_name = 'project'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context = PageProcessor().decorate(context, self.request)

        project = self.get_object()
        sprint_id = self.request.GET.get('sprint')
        sprints = Sprint.objects.filter(project=project).order_by('-start_time')

        selected_sprint = None
        if sprint_id:
            try:
                selected_sprint = sprints.get(id=sprint_id)
            except Sprint.DoesNotExist:
                selected_sprint = None

        columns = Column.objects.filter(project=project).order_by('position').prefetch_related('tasks')

        context.update({
            'columns': columns,
            'sprints': sprints,
            'selected_sprint': selected_sprint
        })
        return context


class ProjectListView(LoginRequiredMixin, ListView):
    model = Project
    template_name = 'kanban/board_list.html'
    context_object_name = 'projects'

    def get_queryset(self):
        user = self.request.user
        if isinstance(user, AnonymousUser) or not user.is_authenticated:
            return Project.objects.none()

        return Project.objects.filter(
            Q(owner=user) |
            Q(collaborators=user)
        ).distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context = PageProcessor().decorate(context, self.request)
        return context
