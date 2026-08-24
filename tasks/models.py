from django.conf import settings
from django.db import models
from django.db.models import Q
from django.urls import reverse


class ProjectQuerySet(models.QuerySet):
    """Membership rules live here so views and templates cannot drift apart."""

    def visible_to(self, user):
        """Projects the user owns, or is assigned at least one task in.

        This is the definition of "project member" used throughout the app.
        `distinct()` is required because the join to tasks yields one row per
        matching task.
        """
        return self.filter(
            Q(owner=user) | Q(tasks__assigned_to=user)
        ).distinct()

    def owned_by(self, user):
        """Projects the user may edit or delete."""
        return self.filter(owner=user)


class TaskQuerySet(models.QuerySet):
    def visible_to(self, user):
        """Tasks in any project the user is a member of.

        Note this is project-wide, not just the user's own assignments: being
        assigned one task in a project grants visibility of all of them.
        """
        return self.filter(project__in=Project.objects.visible_to(user))

    def editable_by(self, user):
        """Tasks the user may edit or delete -- i.e. owns the project of."""
        return self.filter(project__owner=user)

    def assigned_to_user(self, user):
        """Tasks this user is on the hook for. Backs the dashboard."""
        return self.filter(assigned_to=user)


class Project(models.Model):
    """A container for tasks, owned by exactly one user.

    The owner is the only user allowed to edit or delete the project, or the
    tasks inside it; that rule is enforced in the view layer.
    """

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='projects',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ProjectQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('project-detail', args=[self.pk])

    def is_owned_by(self, user):
        """May this user edit or delete the project and the tasks inside it?"""
        return self.owner_id == user.pk

    def is_member(self, user):
        """May this user view the project and its tasks?"""
        return self.is_owned_by(user) or self.tasks.filter(assigned_to=user).exists()


class Task(models.Model):
    """A unit of work inside a project, optionally assigned to a user."""

    class Status(models.TextChoices):
        TODO = 'todo', 'To Do'
        IN_PROGRESS = 'in_progress', 'In Progress'
        DONE = 'done', 'Done'

    class Priority(models.TextChoices):
        LOW = 'low', 'Low'
        MEDIUM = 'medium', 'Medium'
        HIGH = 'high', 'High'

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.TODO,
    )
    priority = models.CharField(
        max_length=10,
        choices=Priority.choices,
        default=Priority.MEDIUM,
    )
    due_date = models.DateField()
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='tasks',
    )
    # Nullable: a task can sit unassigned, and losing the assignee should not
    # delete the work, so SET_NULL rather than CASCADE.
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_tasks',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TaskQuerySet.as_manager()

    class Meta:
        ordering = ['due_date', 'id']
        indexes = [
            # The one deliberate index in this project: it serves the
            # overdue-tasks query (open status, due_date in the past).
            #
            # Column order matters. The query must express the status filter
            # as `status__in=[todo, in_progress]`, not `!= done`. With an
            # inequality on the leading column MySQL cannot pin it to discrete
            # values, so due_date is unusable as a second key and the plan
            # degrades to a full scan. See the write-up for measured EXPLAIN
            # output on both forms.
            models.Index(
                fields=['status', 'due_date'],
                name='task_status_due_date_idx',
            ),
        ]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse('task-detail', args=[self.pk])


class Comment(models.Model):
    """Append-only note on a task. No edit or delete path by design."""

    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name='comments',
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='comments',
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'Comment by {self.author} on {self.task}'
