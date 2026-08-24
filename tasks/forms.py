from django import forms

from .models import Project, Task


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        # `owner` is deliberately absent: it is set from request.user in the
        # view, so a crafted POST cannot create a project owned by someone else.
        fields = ['name', 'description']
        widgets = {'description': forms.Textarea(attrs={'rows': 4})}


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        # `project` is deliberately absent too. It comes from the URL, so a
        # crafted POST cannot move a task into a project the user does not own
        # (which would otherwise be a way to write into someone else's project).
        fields = ['title', 'description', 'status', 'priority', 'due_date', 'assigned_to']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
            'due_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The brief is explicit that a task may be assigned to any user, not
        # just the project owner or existing members.
        self.fields['assigned_to'].empty_label = 'Unassigned'
