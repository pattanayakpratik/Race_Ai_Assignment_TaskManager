from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def dashboard(request):
    """Logged-in landing page.

    Placeholder for now: it exists so login/registration have somewhere to
    redirect to and so @login_required has something to protect. The real
    "my tasks grouped by status" content lands with the dashboard work.
    """
    return render(request, 'tasks/dashboard.html')
