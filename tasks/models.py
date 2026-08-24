from django.conf import settings
from django.db import models
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone


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

    def with_status_counts(self):
        """Annotate each project with a per-status task count.

        Conditional aggregation: one `COUNT(CASE WHEN status = ... END)` per
        status, so a list of projects costs a single query rather than one per
        row. Adds `todo_count`, `in_progress_count`, `done_count`.

        Do NOT chain this onto `visible_to()`; use `visible_to_with_counts()`.
        """
        return self.annotate(**{
            f'{status}_count': Count('tasks', filter=Q(tasks__status=status))
            for status in Task.Status.values
        })

    def visible_to_with_counts(self, user):
        """Projects the user can see, each with correct per-status counts.

        The membership filter and the count annotation both touch `tasks`, and
        combining them directly gives wrong numbers -- in both directions.
        Measured on a project holding 4 todo / 1 in progress / 1 done, seen by
        a user assigned 3 of those tasks:

            visible_to(u).with_status_counts()   -> 3 / 0 / 0   (undercount:
                the annotation reuses the membership join, so it only counts
                rows that satisfied the assignment filter)
            with_status_counts().visible_to(u)   -> 12 / 3 / 3  (overcount:
                the join fans out to one row per assignment, tripling every
                count)

        Resolving membership to a set of ids first gives the annotation a clean
        single join to `tasks` and the right answer: 4 / 1 / 1. It is still two
        queries in total, constant in the number of projects.
        """
        return self.filter(
            pk__in=self.visible_to(user).values('pk')
        ).with_status_counts()


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

    def overdue(self, today=None):
        """Tasks past their due date that are not finished.

        The single reusable definition of "overdue"; the dashboard filter, the
        overdue badge, and the tests all come through here.

        `status__in=open_statuses()` rather than `.exclude(status=DONE)` is a
        deliberate, measured choice. `!= 'done'` is an inequality on the
        leading column of the (status, due_date) index, which leaves MySQL
        unable to use `due_date` as a second key and drops it to a full scan.
        Pinning status to discrete values keeps both columns usable: measured
        on 50k rows, 1,457 rows examined instead of 49,989. The status list is
        derived from the choices, so adding a status keeps this correct.
        """
        return self.filter(
            due_date__lt=today or timezone.localdate(),
            status__in=Task.open_statuses(),
        )

    def status_counts(self):
        """Rows of {status, count}, grouped and counted by the database.

        `.values('status').annotate(...)` is a GROUP BY: it returns one row per
        distinct status, never the task rows themselves. Statuses with no tasks
        are simply absent -- callers that need a zero fill it in.
        """
        return self.values('status').annotate(count=Count('id')).order_by('status')


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

    def status_counts(self):
        """This project's task counts per status, as one row per status.

        One GROUP BY query, zero-filled in Python so every status appears in
        choices order. Filling absent statuses is not the same as counting in
        Python: the counting itself is done by the database.
        """
        counted = {
            row['status']: row['count']
            for row in self.tasks.status_counts()
        }
        return [
            {'status': status, 'label': label, 'count': counted.get(status, 0)}
            for status, label in Task.Status.choices
        ]


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

    @classmethod
    def open_statuses(cls):
        """Every status that is not Done, derived from the choices."""
        return [s for s in cls.Status.values if s != cls.Status.DONE]

    def get_absolute_url(self):
        return reverse('task-detail', args=[self.pk])

    @property
    def is_overdue(self):
        """Same rule as TaskQuerySet.overdue(), for a single loaded row.

        Pure Python on an already-fetched object, so rendering a badge per row
        costs no queries.
        """
        return (
            self.status != self.Status.DONE
            and self.due_date < timezone.localdate()
        )


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
