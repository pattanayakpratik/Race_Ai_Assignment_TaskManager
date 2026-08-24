from django.contrib.auth import views as auth_views
from django.urls import path

from .views import RegisterView

# Only the three routes the brief asks for. django.contrib.auth.urls would also
# pull in the password change/reset flows, which are out of scope here and would
# raise TemplateDoesNotExist if anyone followed them, so they are left out
# rather than wired up half-finished.
urlpatterns = [
    path(
        'login/',
        auth_views.LoginView.as_view(redirect_authenticated_user=True),
        name='login',
    ),
    # LogoutView has been POST-only since Django 5.0, so templates log out
    # through a small form rather than a link.
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('register/', RegisterView.as_view(), name='register'),
]
