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
from django.urls import reverse

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
