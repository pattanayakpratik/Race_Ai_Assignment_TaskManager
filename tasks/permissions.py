"""View-layer permission enforcement.

Every rule in the brief is enforced here, in `dispatch()`, before the view body
or the form runs. That means it applies to GET and POST alike: a direct POST
from a non-owner is rejected regardless of what the templates chose to render.
Hiding buttons in templates is presentation only and is never the control.
"""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from django.utils.functional import cached_property

from .models import Project, Task


class PermissionRequiredMixin(LoginRequiredMixin):
    """Runs `has_permission()` on every request, before the view body.

    Anonymous users fall through to LoginRequiredMixin and get redirected to
    the login page. An authenticated user who fails the check gets a 403 --
    not a redirect, and not a 404: they exist, they are simply not allowed.
    """

    permission_denied_message = 'You are not allowed to do that.'

    def has_permission(self):
        raise NotImplementedError

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if not self.has_permission():
            raise PermissionDenied(self.permission_denied_message)
        return super().dispatch(request, *args, **kwargs)


class ProjectAccessMixin(PermissionRequiredMixin):
    """Resolves the project this view acts on, once per request."""

    #: URL kwarg holding the project's primary key.
    project_url_kwarg = 'pk'

    def get_project_queryset(self):
        """Base queryset for the project. Views that render related rows
        override this to pull those in at the same time."""
        # select_related('owner'): every permission check reads owner, and the
        # templates render the owner's username.
        return Project.objects.select_related('owner')

    @cached_property
    def project(self):
        return get_object_or_404(
            self.get_project_queryset(),
            pk=self.kwargs[self.project_url_kwarg],
        )


class ProjectMemberRequiredMixin(ProjectAccessMixin):
    """Read access: the owner, or anyone assigned a task in the project."""

    permission_denied_message = 'You are not a member of this project.'

    def has_permission(self):
        return self.project.is_member(self.request.user)


class ProjectOwnerRequiredMixin(ProjectAccessMixin):
    """Write access: the owner alone."""

    permission_denied_message = 'Only the project owner can do that.'

    def has_permission(self):
        return self.project.is_owned_by(self.request.user)


class TaskAccessMixin(PermissionRequiredMixin):
    """Resolves the task, and the project whose ownership governs it."""

    def get_task_queryset(self):
        """Base queryset for the task; overridden where comments are rendered."""
        # Forward FKs followed on every request: the project (and its owner)
        # for the permission check, the assignee for display.
        return Task.objects.select_related('project', 'project__owner', 'assigned_to')

    @cached_property
    def task(self):
        return get_object_or_404(
            self.get_task_queryset(),
            pk=self.kwargs['pk'],
        )

    @property
    def project(self):
        return self.task.project


class TaskViewableRequiredMixin(TaskAccessMixin):
    """Read access to a task follows membership of its project."""

    permission_denied_message = 'You are not a member of this task\'s project.'

    def has_permission(self):
        return self.project.is_member(self.request.user)


class TaskEditableRequiredMixin(TaskAccessMixin):
    """Write access to a task belongs to the project owner, not the assignee."""

    permission_denied_message = 'Only the project owner can change this task.'

    def has_permission(self):
        return self.project.is_owned_by(self.request.user)
