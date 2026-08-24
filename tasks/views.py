from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render
from django.urls import reverse, reverse_lazy
from django.views.generic import (
    CreateView, DeleteView, DetailView, ListView, UpdateView,
)

from .forms import CommentForm, ProjectForm, TaskForm
from .models import Project, Task
from .permissions import (
    ProjectMemberRequiredMixin,
    ProjectOwnerRequiredMixin,
    TaskEditableRequiredMixin,
    TaskViewableRequiredMixin,
)


@login_required
def dashboard(request):
    """Logged-in landing page.

    Placeholder for now: it exists so login/registration have somewhere to
    redirect to and so @login_required has something to protect. The real
    "my tasks grouped by status" content lands with the dashboard work.
    """
    return render(request, 'tasks/dashboard.html')


# --------------------------------------------------------------------------
# Projects
# --------------------------------------------------------------------------

class ProjectListView(LoginRequiredMixin, ListView):
    """Projects the user is a member of. Scoped by queryset, not by template."""

    template_name = 'tasks/project_list.html'
    context_object_name = 'projects'

    def get_queryset(self):
        # select_related('owner') so rendering the owner column does not issue
        # one query per row.
        return Project.objects.visible_to(self.request.user).select_related('owner')


class ProjectDetailView(ProjectMemberRequiredMixin, DetailView):
    template_name = 'tasks/project_detail.html'
    context_object_name = 'project'

    def get_object(self, queryset=None):
        # Already fetched by the permission mixin; do not query again.
        return self.project

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # select_related('assigned_to') keeps the task table to one query
        # rather than one per row for the assignee column.
        context['tasks'] = (
            self.project.tasks.select_related('assigned_to').all()
        )
        context['can_edit'] = self.project.is_owned_by(self.request.user)
        return context


class ProjectCreateView(LoginRequiredMixin, CreateView):
    """Any authenticated user may create a project; they become its owner."""

    form_class = ProjectForm
    template_name = 'tasks/project_form.html'

    def form_valid(self, form):
        form.instance.owner = self.request.user
        return super().form_valid(form)


class ProjectUpdateView(ProjectOwnerRequiredMixin, UpdateView):
    form_class = ProjectForm
    template_name = 'tasks/project_form.html'

    def get_object(self, queryset=None):
        return self.project


class ProjectDeleteView(ProjectOwnerRequiredMixin, DeleteView):
    template_name = 'tasks/project_confirm_delete.html'
    context_object_name = 'project'
    success_url = reverse_lazy('project-list')

    def get_object(self, queryset=None):
        return self.project


# --------------------------------------------------------------------------
# Tasks
# --------------------------------------------------------------------------

class TaskPageContextMixin:
    """Context for the task page.

    Shared by the detail view and by the comment view that posts to it, so a
    failed comment can be re-rendered on the same page without the two drifting
    apart.
    """

    def task_page_context(self):
        return {
            'task': self.task,
            # One query for all comments plus their authors, rather than one
            # per comment row.
            'comments': self.task.comments.select_related('author').all(),
            'can_edit': self.project.is_owned_by(self.request.user),
        }


class TaskDetailView(TaskViewableRequiredMixin, TaskPageContextMixin, DetailView):
    template_name = 'tasks/task_detail.html'
    context_object_name = 'task'

    def get_object(self, queryset=None):
        return self.task

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.task_page_context())
        # Anyone who can see the task can comment on it, so the form is always
        # rendered here.
        context.setdefault('comment_form', CommentForm())
        return context


class TaskCreateView(ProjectOwnerRequiredMixin, CreateView):
    """Tasks are created inside a project, by that project's owner.

    Creation is owner-only for the same reason edit and delete are: a member
    who could add a task would immediately be unable to change or remove it.
    """

    form_class = TaskForm
    template_name = 'tasks/task_form.html'
    project_url_kwarg = 'project_pk'

    def form_valid(self, form):
        # Project comes from the URL, which the mixin has already checked the
        # user owns -- never from the submitted form data.
        form.instance.project = self.project
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = self.project
        return context


class TaskUpdateView(TaskEditableRequiredMixin, UpdateView):
    form_class = TaskForm
    template_name = 'tasks/task_form.html'

    def get_object(self, queryset=None):
        return self.task

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = self.project
        return context


class TaskDeleteView(TaskEditableRequiredMixin, DeleteView):
    template_name = 'tasks/task_confirm_delete.html'
    context_object_name = 'task'

    def get_object(self, queryset=None):
        return self.task

    def get_success_url(self):
        return reverse('project-detail', args=[self.project.pk])


# --------------------------------------------------------------------------
# Comments
# --------------------------------------------------------------------------

class CommentCreateView(TaskViewableRequiredMixin, TaskPageContextMixin, CreateView):
    """Append a comment to a task.

    Permission is deliberately the *view* rule, not the edit rule: the brief
    says any authenticated user who can view a task can comment on it. So
    project members can comment on tasks they cannot edit -- including tasks
    assigned to someone else -- while non-members are refused.

    POST only: the form itself lives on the task detail page.
    """

    form_class = CommentForm
    template_name = 'tasks/task_detail.html'
    http_method_names = ['post']

    def form_valid(self, form):
        # Both FKs are set server-side, never from the submitted data.
        form.instance.task = self.task
        form.instance.author = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return self.task.get_absolute_url()

    def get_context_data(self, **kwargs):
        # Only reached when the form is invalid (e.g. an empty body): re-render
        # the task page with the bound form so the errors are visible.
        context = super().get_context_data(**kwargs)
        context.update(self.task_page_context())
        context['comment_form'] = context['form']
        return context
