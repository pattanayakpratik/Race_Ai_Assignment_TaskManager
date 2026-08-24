from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class AuthFlowTests(TestCase):
    """Covers the three routes the brief asks for: register, log in, log out."""

    credentials = {'username': 'alice', 'password': 'sufficiently-long-pw-42'}

    def test_register_creates_user_and_signs_them_in(self):
        response = self.client.post(reverse('register'), {
            'username': 'alice',
            'password1': self.credentials['password'],
            'password2': self.credentials['password'],
        })

        self.assertRedirects(response, reverse('dashboard'))
        self.assertTrue(User.objects.filter(username='alice').exists())
        self.assertEqual(int(self.client.session['_auth_user_id']),
                         User.objects.get(username='alice').pk)

    def test_register_rejects_mismatched_passwords(self):
        response = self.client.post(reverse('register'), {
            'username': 'bob',
            'password1': self.credentials['password'],
            'password2': 'something-else-entirely',
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='bob').exists())

    def test_register_applies_password_validators(self):
        """Password policy comes from AUTH_PASSWORD_VALIDATORS, not our code."""
        response = self.client.post(reverse('register'), {
            'username': 'carol', 'password1': '123', 'password2': '123',
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='carol').exists())

    def test_login_and_logout(self):
        User.objects.create_user(**self.credentials)

        response = self.client.post(reverse('login'), self.credentials)
        self.assertRedirects(response, reverse('dashboard'))
        self.assertIn('_auth_user_id', self.client.session)

        response = self.client.post(reverse('logout'))
        self.assertRedirects(response, reverse('login'))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_logout_rejects_get(self):
        """LogoutView is POST-only since Django 5.0; templates use a form."""
        User.objects.create_user(**self.credentials)
        self.client.force_login(User.objects.get(username='alice'))

        self.assertEqual(self.client.get(reverse('logout')).status_code, 405)
        self.assertIn('_auth_user_id', self.client.session)

    def test_login_rejects_bad_password(self):
        User.objects.create_user(**self.credentials)

        response = self.client.post(reverse('login'), {
            'username': 'alice', 'password': 'wrong',
        })

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_anonymous_user_is_redirected_to_login_with_next(self):
        response = self.client.get(reverse('dashboard'))

        self.assertRedirects(
            response, f"{reverse('login')}?next={reverse('dashboard')}"
        )

    def test_authenticated_user_reaches_dashboard(self):
        User.objects.create_user(**self.credentials)
        self.client.force_login(User.objects.get(username='alice'))

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'alice')
