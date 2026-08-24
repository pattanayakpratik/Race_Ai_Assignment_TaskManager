from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Prefetch
from django.shortcuts import render
from django.urls import reverse, reverse_lazy
from django.views.generic import (
    CreateView, DeleteView, DetailView, ListView, UpdateView,
)

from .forms import CommentForm, ProjectForm, TaskForm
from .models import Comment, Project, Task
from .permissions import (
    ProjectMemberRequiredMixin,
    ProjectOwnerRequiredMixin,
    TaskEditableRequiredMixin,
    TaskViewableRequiredMixin,
)


@login_required
def dashboard(request):
    """Logged-in landing page: the current user's tasks, grouped by status.

    One query fetches every task assigned to the user; the grouping into
    columns happens in Python over those already-loaded rows. That is
    deliberate -- three status-filtered querysets would mean three round trips
    to fetch the same set of rows.

    select_related('project') is what keeps it to one query: each row renders
    its project name, which would otherwise be a query per task.
    """
    tasks = (
        Task.objects
        .assigned_to_user(request.user)
        .select_related('project')
    )

    # "Overdue" filter: narrows the board to work that is late, using the one
    # reusable overdue() definition on the queryset.
    show_overdue_only = request.GET.get('filter') == 'overdue'
    if show_overdue_only:
        tasks = tasks.overdue()

    # Counted in the database, not by walking the rows above -- the board may
    # already be filtered, and this figure must reflect the whole board.
    overdue_count = Task.objects.assigned_to_user(request.user).overdue().count()

    # Pre-seed every status so empty columns still render.
    grouped = {status: [] for status in Task.Status.values}
    for task in tasks:
        grouped[task.status].append(task)

    # Task.Status.choices drives the column order, so adding a status to the
    # model adds a column here without touching this view or the template.
    columns = [
        {'status': status, 'label': label, 'tasks': grouped[status]}
        for status, label in Task.Status.choices
    ]

    return render(request, 'tasks/dashboard.html', {
        'columns': columns,
        'task_count': len(tasks),
        'overdue_count': overdue_count,
        'show_overdue_only': show_overdue_only,
    })


# --------------------------------------------------------------------------
# Projects
# --------------------------------------------------------------------------

class ProjectListView(LoginRequiredMixin, ListView):
    """Projects the user is a member of. Scoped by queryset, not by template."""

    template_name = 'tasks/project_list.html'
    context_object_name = 'projects'

    def get_queryset(self):
        # select_related('owner') so rendering the owner column does not issue
        # one query per row; visible_to_with_counts() adds the per-status
        # totals in the same query rather than one aggregate per project.
        return (
            Project.objects
            .visible_to_with_counts(self.request.user)
            .select_related('owner')
        )


class ProjectDetailView(ProjectMemberRequiredMixin, DetailView):
    template_name = 'tasks/project_detail.html'
    context_object_name = 'project'

    def get_project_queryset(self):
        # `tasks` is a reverse FK -- a to-many relation -- so it needs
        # prefetch_related, not select_related. The inner select_related pulls
        # each task's assignee in the same query, so rendering the assignee
        # column costs nothing per row.
        return super().get_project_queryset().prefetch_related(
            Prefetch(
                'tasks',
                queryset=Task.objects.select_related('assigned_to'),
            )
        )

    def get_object(self, queryset=None):
        # Already fetched by the permission mixin; do not query again.
        return self.project

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # .all() reads the prefetch cache -- no query here.
        context['tasks'] = self.project.tasks.all()
        context['can_edit'] = self.project.is_owned_by(self.request.user)
        # One GROUP BY query for the whole breakdown.
        context['status_counts'] = self.project.status_counts()
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
    """Context for the task page, plus the fetching it depends on.

    Shared by the detail view and by the comment view that posts to it, so a
    failed comment can be re-rendered on the same page without the two drifting
    apart.

    Must be listed BEFORE the permission mixin in a view's bases: it overrides
    get_task_queryset(), and TaskAccessMixin defines the same method. Listed
    after, TaskAccessMixin wins the MRO, the prefetch below never runs, and
    rendering the comment authors silently becomes N+1.
    """

    def get_task_queryset(self):
        # `comments` is a reverse FK, so prefetch_related; select_related on
        # the inner queryset brings each comment's author along with it.
        return super().get_task_queryset().prefetch_related(
            Prefetch(
                'comments',
                queryset=Comment.objects.select_related('author'),
            )
        )

    def task_page_context(self):
        return {
            'task': self.task,
            # .all() reads the prefetch cache -- no query, and no per-comment
            # query for the author.
            'comments': self.task.comments.all(),
            'can_edit': self.project.is_owned_by(self.request.user),
        }


class TaskDetailView(TaskPageContextMixin, TaskViewableRequiredMixin, DetailView):
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

class CommentCreateView(TaskPageContextMixin, TaskViewableRequiredMixin, CreateView):
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
