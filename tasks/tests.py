"""Permission tests for Project and Task CRUD.

The brief's central rule: ownership must hold at the view layer, so a direct
POST from a non-owner has to fail regardless of what the templates render.
Every mutating endpoint is therefore exercised by POST as each kind of actor,
and each test asserts the database is unchanged, not merely that a 403 came
back.

Actors used throughout:
  owner    -- owns the project
  member   -- assigned a task in the project, so may view but not modify
  outsider -- unrelated authenticated user
  anonymous
"""
import datetime

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from .forms import CommentForm
from .models import Comment, Project, Task

DUE = datetime.date(2026, 12, 1)


class PermissionTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user('owner', password='pw-for-tests-1')
        cls.member = User.objects.create_user('member', password='pw-for-tests-2')
        cls.outsider = User.objects.create_user('outsider', password='pw-for-tests-3')

        cls.project = Project.objects.create(
            name='Apollo', description='Original', owner=cls.owner,
        )
        # `member` becomes a project member by virtue of this assignment.
        cls.assigned_task = Task.objects.create(
            title='Assigned task', due_date=DUE,
            project=cls.project, assigned_to=cls.member,
        )
        # Not assigned to anyone: members can still see it.
        cls.other_task = Task.objects.create(
            title='Unassigned task', due_date=DUE, project=cls.project,
        )
        # A separate project the outsider owns, used to test cross-project moves.
        cls.foreign_project = Project.objects.create(
            name='Gemini', owner=cls.outsider,
        )

    def task_payload(self, **overrides):
        payload = {
            'title': 'Rewritten', 'description': '',
            'status': Task.Status.DONE, 'priority': Task.Priority.HIGH,
            'due_date': '2027-01-01', 'assigned_to': '',
        }
        payload.update(overrides)
        return payload


class ProjectPermissionTests(PermissionTestCase):

    # -- read ---------------------------------------------------------------

    def test_owner_and_member_can_view_project(self):
        for user in (self.owner, self.member):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(
                    reverse('project-detail', args=[self.project.pk])
                )
                self.assertEqual(response.status_code, 200)

    def test_outsider_cannot_view_project(self):
        self.client.force_login(self.outsider)
        response = self.client.get(
            reverse('project-detail', args=[self.project.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_project_list_is_scoped_to_membership(self):
        expectations = [
            (self.owner, True), (self.member, True), (self.outsider, False),
        ]
        for user, should_see in expectations:
            with self.subTest(user=user.username):
                self.client.force_login(user)
                projects = self.client.get(
                    reverse('project-list')
                ).context['projects']
                self.assertEqual(self.project in projects, should_see)

    def test_project_list_does_not_duplicate_multi_task_projects(self):
        """visible_to() joins tasks, so a second assignment must not dupe rows."""
        Task.objects.create(
            title='Second assignment', due_date=DUE,
            project=self.project, assigned_to=self.member,
        )
        self.client.force_login(self.member)
        projects = self.client.get(reverse('project-list')).context['projects']
        self.assertEqual(list(projects).count(self.project), 1)

    # -- write --------------------------------------------------------------

    def test_owner_can_update_project(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse('project-update', args=[self.project.pk]),
            {'name': 'Apollo II', 'description': 'Edited'},
        )
        self.project.refresh_from_db()
        self.assertRedirects(response, self.project.get_absolute_url())
        self.assertEqual(self.project.name, 'Apollo II')

    def test_non_owner_post_to_update_is_forbidden_and_changes_nothing(self):
        for user in (self.member, self.outsider):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.post(
                    reverse('project-update', args=[self.project.pk]),
                    {'name': 'Hijacked', 'description': 'Hijacked'},
                )
                self.project.refresh_from_db()
                self.assertEqual(response.status_code, 403)
                self.assertEqual(self.project.name, 'Apollo')
                self.assertEqual(self.project.description, 'Original')

    def test_owner_can_delete_project(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse('project-delete', args=[self.project.pk])
        )
        self.assertRedirects(response, reverse('project-list'))
        self.assertFalse(Project.objects.filter(pk=self.project.pk).exists())

    def test_non_owner_post_to_delete_is_forbidden_and_project_survives(self):
        for user in (self.member, self.outsider):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.post(
                    reverse('project-delete', args=[self.project.pk])
                )
                self.assertEqual(response.status_code, 403)
                self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())

    def test_any_authenticated_user_can_create_a_project_and_owns_it(self):
        self.client.force_login(self.outsider)
        self.client.post(reverse('project-create'), {
            'name': 'Fresh', 'description': '',
        })
        self.assertEqual(Project.objects.get(name='Fresh').owner, self.outsider)

    def test_owner_cannot_be_spoofed_on_create(self):
        """`owner` is not a form field, so a crafted POST cannot set it."""
        self.client.force_login(self.outsider)
        self.client.post(reverse('project-create'), {
            'name': 'Spoofed', 'description': '', 'owner': self.owner.pk,
        })
        self.assertEqual(Project.objects.get(name='Spoofed').owner, self.outsider)

    # -- anonymous ----------------------------------------------------------

    def test_anonymous_is_redirected_to_login(self):
        urls = [
            reverse('project-list'),
            reverse('project-create'),
            reverse('project-detail', args=[self.project.pk]),
            reverse('project-update', args=[self.project.pk]),
            reverse('project-delete', args=[self.project.pk]),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertRedirects(response, f"{reverse('login')}?next={url}")

    def test_anonymous_post_does_not_mutate(self):
        self.client.post(
            reverse('project-update', args=[self.project.pk]),
            {'name': 'Hijacked', 'description': ''},
        )
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, 'Apollo')


class TaskPermissionTests(PermissionTestCase):

    # -- read ---------------------------------------------------------------

    def test_members_can_view_any_task_in_the_project(self):
        """Membership is project-wide, not limited to your own assignments."""
        self.client.force_login(self.member)
        for task in (self.assigned_task, self.other_task):
            with self.subTest(task=task.title):
                response = self.client.get(
                    reverse('task-detail', args=[task.pk])
                )
                self.assertEqual(response.status_code, 200)

    def test_outsider_cannot_view_task(self):
        self.client.force_login(self.outsider)
        response = self.client.get(
            reverse('task-detail', args=[self.assigned_task.pk])
        )
        self.assertEqual(response.status_code, 403)

    # -- create -------------------------------------------------------------

    def test_owner_can_create_task_in_own_project(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse('task-create', args=[self.project.pk]),
            self.task_payload(title='Brand new'),
        )
        task = Task.objects.get(title='Brand new')
        self.assertRedirects(response, task.get_absolute_url())
        self.assertEqual(task.project, self.project)

    def test_non_owner_cannot_create_task_in_another_users_project(self):
        for user in (self.member, self.outsider):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                title = f'Sneaky {user.username}'
                response = self.client.post(
                    reverse('task-create', args=[self.project.pk]),
                    self.task_payload(title=title),
                )
                self.assertEqual(response.status_code, 403)
                self.assertFalse(Task.objects.filter(title=title).exists())

    def test_task_can_be_assigned_to_any_user(self):
        """The brief is explicit: not just the owner, and not just members."""
        self.client.force_login(self.owner)
        self.client.post(
            reverse('task-create', args=[self.project.pk]),
            self.task_payload(title='For an outsider', assigned_to=self.outsider.pk),
        )
        self.assertEqual(
            Task.objects.get(title='For an outsider').assigned_to, self.outsider
        )

    # -- update / delete ----------------------------------------------------

    def test_owner_can_update_task(self):
        self.client.force_login(self.owner)
        self.client.post(
            reverse('task-update', args=[self.assigned_task.pk]),
            self.task_payload(),
        )
        self.assigned_task.refresh_from_db()
        self.assertEqual(self.assigned_task.title, 'Rewritten')
        self.assertEqual(self.assigned_task.status, Task.Status.DONE)

    def test_assignee_cannot_update_their_own_task(self):
        """Being the assignee grants read access, never write access."""
        self.client.force_login(self.member)
        response = self.client.post(
            reverse('task-update', args=[self.assigned_task.pk]),
            self.task_payload(),
        )
        self.assigned_task.refresh_from_db()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.assigned_task.title, 'Assigned task')

    def test_outsider_cannot_update_task(self):
        self.client.force_login(self.outsider)
        response = self.client.post(
            reverse('task-update', args=[self.assigned_task.pk]),
            self.task_payload(),
        )
        self.assigned_task.refresh_from_db()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.assigned_task.title, 'Assigned task')

    def test_owner_can_delete_task(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse('task-delete', args=[self.assigned_task.pk])
        )
        self.assertRedirects(response, self.project.get_absolute_url())
        self.assertFalse(Task.objects.filter(pk=self.assigned_task.pk).exists())

    def test_non_owner_cannot_delete_task(self):
        for user in (self.member, self.outsider):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.post(
                    reverse('task-delete', args=[self.assigned_task.pk])
                )
                self.assertEqual(response.status_code, 403)
                self.assertTrue(
                    Task.objects.filter(pk=self.assigned_task.pk).exists()
                )

    def test_task_cannot_be_moved_into_another_project_by_crafted_post(self):
        """`project` is not a form field, so a POST cannot relocate a task."""
        self.client.force_login(self.owner)
        self.client.post(
            reverse('task-update', args=[self.assigned_task.pk]),
            self.task_payload(project=self.foreign_project.pk),
        )
        self.assigned_task.refresh_from_db()
        self.assertEqual(self.assigned_task.project, self.project)

    def test_anonymous_post_does_not_mutate_task(self):
        self.client.post(
            reverse('task-delete', args=[self.assigned_task.pk])
        )
        self.assertTrue(Task.objects.filter(pk=self.assigned_task.pk).exists())


class CascadeTests(PermissionTestCase):
    def test_deleting_a_project_removes_its_tasks_and_comments(self):
        Comment.objects.create(
            task=self.assigned_task, author=self.member, body='note'
        )
        self.client.force_login(self.owner)
        self.client.post(reverse('project-delete', args=[self.project.pk]))

        self.assertFalse(Task.objects.filter(project=self.project).exists())
        self.assertFalse(Comment.objects.filter(task=self.assigned_task).exists())


class MemberAccessTests(PermissionTestCase):
    """The membership rule: owner, or anyone assigned a task in the project."""

    def test_assignment_is_what_confers_membership(self):
        """Before assignment the user is an outsider; after it, a member."""
        stranger = User.objects.create_user('stranger', password='pw-for-tests-4')
        url = reverse('project-detail', args=[self.project.pk])

        self.client.force_login(stranger)
        self.assertEqual(self.client.get(url).status_code, 403)

        self.other_task.assigned_to = stranger
        self.other_task.save(update_fields=['assigned_to'])

        self.assertEqual(self.client.get(url).status_code, 200)

    def test_unassignment_revokes_membership(self):
        """Membership is derived, not stored, so removing the assignment removes access."""
        url = reverse('project-detail', args=[self.project.pk])

        self.client.force_login(self.member)
        self.assertEqual(self.client.get(url).status_code, 200)

        self.assigned_task.assigned_to = None
        self.assigned_task.save(update_fields=['assigned_to'])

        self.assertEqual(self.client.get(url).status_code, 403)

    def test_member_sees_every_task_in_the_project(self):
        """Including tasks assigned to someone else, or to nobody."""
        self.client.force_login(self.member)
        response = self.client.get(
            reverse('project-detail', args=[self.project.pk])
        )
        listed = list(response.context['tasks'])
        self.assertIn(self.assigned_task, listed)
        self.assertIn(self.other_task, listed)


class CommentPermissionTests(PermissionTestCase):
    """Anyone who can view a task can comment on it -- view rule, not edit rule."""

    def comment_url(self, task):
        return reverse('comment-create', args=[task.pk])

    def test_owner_can_comment(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            self.comment_url(self.assigned_task), {'body': 'From the owner'}
        )
        self.assertRedirects(response, self.assigned_task.get_absolute_url())
        self.assertEqual(
            Comment.objects.get(body='From the owner').author, self.owner
        )

    def test_member_can_comment_on_their_own_task(self):
        self.client.force_login(self.member)
        self.client.post(
            self.comment_url(self.assigned_task), {'body': 'From the assignee'}
        )
        self.assertEqual(
            Comment.objects.get(body='From the assignee').author, self.member
        )

    def test_member_can_comment_on_a_task_they_cannot_edit(self):
        """The point of the rule: comment access follows viewing, not ownership."""
        self.client.force_login(self.member)
        self.client.post(
            self.comment_url(self.other_task), {'body': 'On someone elses task'}
        )
        comment = Comment.objects.get(body='On someone elses task')
        self.assertEqual(comment.task, self.other_task)

        # Same user, same task: commenting allowed, editing still refused.
        response = self.client.post(
            reverse('task-update', args=[self.other_task.pk]),
            self.task_payload(),
        )
        self.assertEqual(response.status_code, 403)

    def test_outsider_cannot_comment(self):
        self.client.force_login(self.outsider)
        response = self.client.post(
            self.comment_url(self.assigned_task), {'body': 'Should not land'}
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Comment.objects.filter(body='Should not land').exists())

    def test_anonymous_cannot_comment(self):
        response = self.client.post(
            self.comment_url(self.assigned_task), {'body': 'Should not land'}
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Comment.objects.filter(body='Should not land').exists())

    def test_author_cannot_be_spoofed(self):
        """`author` is not a form field; it is always request.user."""
        self.client.force_login(self.member)
        self.client.post(self.comment_url(self.assigned_task), {
            'body': 'Spoof attempt', 'author': self.owner.pk,
        })
        self.assertEqual(
            Comment.objects.get(body='Spoof attempt').author, self.member
        )

    def test_task_cannot_be_spoofed(self):
        """`task` comes from the URL, so a POST cannot redirect the comment."""
        foreign_task = Task.objects.create(
            title='Foreign', due_date=DUE, project=self.foreign_project,
        )
        self.client.force_login(self.member)
        self.client.post(self.comment_url(self.assigned_task), {
            'body': 'Wrong task', 'task': foreign_task.pk,
        })
        self.assertEqual(
            Comment.objects.get(body='Wrong task').task, self.assigned_task
        )

    def test_empty_comment_is_rejected(self):
        self.client.force_login(self.member)
        response = self.client.post(self.comment_url(self.assigned_task), {'body': ''})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Comment.objects.filter(task=self.assigned_task).exists())
        self.assertTrue(response.context['comment_form'].errors)

    def test_comment_form_is_shown_to_every_member(self):
        for user in (self.owner, self.member):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(
                    reverse('task-detail', args=[self.assigned_task.pk])
                )
                self.assertContains(response, self.comment_url(self.assigned_task))

    def test_comments_are_append_only(self):
        """No edit or delete route exists for a comment, by design."""
        comment = Comment.objects.create(
            task=self.assigned_task, author=self.member, body='permanent'
        )
        for name in ('comment-update', 'comment-delete'):
            with self.subTest(route=name):
                with self.assertRaises(NoReverseMatch):
                    reverse(name, args=[comment.pk])

    def test_comments_appear_in_chronological_order(self):
        self.client.force_login(self.member)
        for body in ('first', 'second', 'third'):
            self.client.post(self.comment_url(self.assigned_task), {'body': body})

        response = self.client.get(
            reverse('task-detail', args=[self.assigned_task.pk])
        )
        self.assertEqual(
            [c.body for c in response.context['comments']],
            ['first', 'second', 'third'],
        )


class DashboardTests(TestCase):
    """The dashboard shows the current user's assignments, grouped by status."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('dash', password='pw-for-tests-1')
        cls.colleague = User.objects.create_user('colleague', password='pw-for-tests-2')
        cls.project = Project.objects.create(name='Apollo', owner=cls.colleague)

        cls.todo = Task.objects.create(
            title='Mine: todo', due_date=DUE, project=cls.project,
            assigned_to=cls.user, status=Task.Status.TODO,
        )
        cls.in_progress = Task.objects.create(
            title='Mine: in progress', due_date=DUE, project=cls.project,
            assigned_to=cls.user, status=Task.Status.IN_PROGRESS,
        )
        cls.done = Task.objects.create(
            title='Mine: done', due_date=DUE, project=cls.project,
            assigned_to=cls.user, status=Task.Status.DONE,
        )
        # Noise the dashboard must exclude.
        cls.someone_elses = Task.objects.create(
            title='Not mine', due_date=DUE, project=cls.project,
            assigned_to=cls.colleague, status=Task.Status.TODO,
        )
        cls.unassigned = Task.objects.create(
            title='Unassigned', due_date=DUE, project=cls.project,
            status=Task.Status.TODO,
        )

    def columns(self, response):
        return {c['label']: list(c['tasks']) for c in response.context['columns']}

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse('dashboard'))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('dashboard')}")

    def test_three_columns_in_workflow_order(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(
            [c['label'] for c in response.context['columns']],
            ['To Do', 'In Progress', 'Done'],
        )

    def test_tasks_land_in_the_column_matching_their_status(self):
        self.client.force_login(self.user)
        columns = self.columns(self.client.get(reverse('dashboard')))
        self.assertEqual(columns['To Do'], [self.todo])
        self.assertEqual(columns['In Progress'], [self.in_progress])
        self.assertEqual(columns['Done'], [self.done])

    def test_only_the_current_users_assignments_are_shown(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('dashboard'))
        shown = [t for column in response.context['columns'] for t in column['tasks']]

        self.assertNotIn(self.someone_elses, shown)
        self.assertNotIn(self.unassigned, shown)
        self.assertEqual(response.context['task_count'], 3)

    def test_each_user_sees_their_own_board(self):
        self.client.force_login(self.colleague)
        columns = self.columns(self.client.get(reverse('dashboard')))
        self.assertEqual(columns['To Do'], [self.someone_elses])
        self.assertEqual(columns['In Progress'], [])

    def test_empty_columns_still_render(self):
        """A user with only To Do work still gets all three headings."""
        Task.objects.filter(
            pk__in=[self.in_progress.pk, self.done.pk]
        ).update(assigned_to=None)

        self.client.force_login(self.user)
        response = self.client.get(reverse('dashboard'))
        columns = self.columns(response)

        self.assertEqual(columns['In Progress'], [])
        self.assertEqual(columns['Done'], [])
        for label in ('To Do', 'In Progress', 'Done'):
            self.assertContains(response, label)

    def test_user_with_no_assignments_sees_a_prompt(self):
        loner = User.objects.create_user('loner', password='pw-for-tests-3')
        self.client.force_login(loner)
        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.context['task_count'], 0)
        self.assertContains(response, 'No tasks are assigned to you yet')

    def test_columns_follow_the_status_choices(self):
        """Column order is derived from the model, not hardcoded in the view."""
        self.client.force_login(self.user)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(
            [c['status'] for c in response.context['columns']],
            list(Task.Status.values),
        )

    def test_tasks_are_ordered_by_due_date_within_a_column(self):
        earlier = Task.objects.create(
            title='Earlier', due_date=DUE - datetime.timedelta(days=7),
            project=self.project, assigned_to=self.user, status=Task.Status.TODO,
        )
        self.client.force_login(self.user)
        columns = self.columns(self.client.get(reverse('dashboard')))
        self.assertEqual(columns['To Do'], [earlier, self.todo])

    def test_dashboard_is_a_fixed_number_of_queries(self):
        """select_related('project') keeps the board off the N+1 path."""
        self.client.force_login(self.user)
        with self.assertNumQueries(3):
            # 1 session, 2 user, 3 the task queryset. Rendering each card's
            # project name adds nothing.
            self.client.get(reverse('dashboard'))

        for i in range(20):
            Task.objects.create(
                title=f'Extra {i}', due_date=DUE,
                project=Project.objects.create(name=f'P{i}', owner=self.colleague),
                assigned_to=self.user, status=Task.Status.TODO,
            )

        with self.assertNumQueries(3):
            self.client.get(reverse('dashboard'))


class CommentAppendOnlyTests(PermissionTestCase):
    """No route, form field, or admin screen may edit or remove a comment."""

    def setUp(self):
        self.comment = Comment.objects.create(
            task=self.assigned_task, author=self.member, body='permanent',
        )

    def test_no_edit_or_delete_route_exists(self):
        for name in ('comment-update', 'comment-delete', 'comment-edit'):
            with self.subTest(route=name):
                with self.assertRaises(NoReverseMatch):
                    reverse(name, args=[self.comment.pk])

    def test_comment_form_exposes_only_the_body(self):
        """Nothing in the form can retarget or reattribute a comment."""
        self.assertEqual(list(CommentForm().fields), ['body'])

    def test_posting_again_appends_rather_than_replaces(self):
        self.client.force_login(self.member)
        self.client.post(
            reverse('comment-create', args=[self.assigned_task.pk]),
            {'body': 'a second thought'},
        )

        bodies = list(
            Comment.objects.filter(task=self.assigned_task)
            .values_list('body', flat=True)
        )
        self.assertEqual(bodies, ['permanent', 'a second thought'])

    def test_task_page_offers_no_edit_or_delete_control_for_comments(self):
        self.client.force_login(self.member)
        html = self.client.get(
            reverse('task-detail', args=[self.assigned_task.pk])
        ).content.decode()

        # The only comment-related form on the page is the append form.
        self.assertEqual(html.count('comments/new/'), 1)
        self.assertNotIn(f'comments/{self.comment.pk}', html)

    def test_admin_cannot_change_a_comment(self):
        admin_user = User.objects.create_superuser(
            'root', 'root@example.com', 'pw-for-tests-admin'
        )
        self.client.force_login(admin_user)

        response = self.client.post(
            reverse('admin:tasks_comment_change', args=[self.comment.pk]),
            {'body': 'rewritten by an admin'},
        )
        self.comment.refresh_from_db()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.comment.body, 'permanent')

    def test_admin_cannot_delete_a_comment(self):
        admin_user = User.objects.create_superuser(
            'root2', 'root2@example.com', 'pw-for-tests-admin'
        )
        self.client.force_login(admin_user)

        response = self.client.post(
            reverse('admin:tasks_comment_delete', args=[self.comment.pk]),
            {'post': 'yes'},
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Comment.objects.filter(pk=self.comment.pk).exists())
