from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView


class RegisterView(CreateView):
    """Thin wrapper around Django's UserCreationForm.

    Django ships login/logout views but no registration view, so this is the
    only piece of auth written by hand -- and it still delegates the user
    creation, password validation, and hashing to django.contrib.auth.
    """

    form_class = UserCreationForm
    template_name = 'registration/register.html'
    success_url = reverse_lazy('dashboard')

    def dispatch(self, request, *args, **kwargs):
        # An already-authenticated user has no business on the signup page.
        if request.user.is_authenticated:
            return redirect(self.success_url)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        # Sign the new account straight in rather than bouncing to /login/.
        login(self.request, self.object)
        return response
