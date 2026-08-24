from django.contrib import admin

from .models import Comment, Project, Task


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ['name', 'owner', 'created_at']
    list_select_related = ['owner']
    search_fields = ['name', 'description']


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ['title', 'project', 'status', 'priority', 'due_date', 'assigned_to']
    list_select_related = ['project', 'assigned_to']
    list_filter = ['status', 'priority']
    search_fields = ['title', 'description']


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    """Comments are append-only here too.

    The app exposes no edit or delete route for a comment, so leaving the admin
    able to rewrite or remove one would be the only way round that rule. Add
    and view stay enabled; change and delete are switched off, which also
    removes the "delete selected" bulk action from the changelist.
    """

    list_display = ['task', 'author', 'created_at']
    list_select_related = ['task', 'author']

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
