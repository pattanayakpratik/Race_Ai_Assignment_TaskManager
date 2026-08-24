from django.urls import path

from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),

    path('projects/', views.ProjectListView.as_view(), name='project-list'),
    path('projects/new/', views.ProjectCreateView.as_view(), name='project-create'),
    path('projects/<int:pk>/', views.ProjectDetailView.as_view(), name='project-detail'),
    path('projects/<int:pk>/edit/', views.ProjectUpdateView.as_view(), name='project-update'),
    path('projects/<int:pk>/delete/', views.ProjectDeleteView.as_view(), name='project-delete'),

    # Tasks are created in the context of a project, so the project id is in
    # the URL rather than the form.
    path(
        'projects/<int:project_pk>/tasks/new/',
        views.TaskCreateView.as_view(),
        name='task-create',
    ),
    path('tasks/<int:pk>/', views.TaskDetailView.as_view(), name='task-detail'),
    path('tasks/<int:pk>/edit/', views.TaskUpdateView.as_view(), name='task-update'),
    path('tasks/<int:pk>/delete/', views.TaskDeleteView.as_view(), name='task-delete'),

    # Comments are append-only, so create is the only route. The form lives on
    # the task detail page and posts here.
    path(
        'tasks/<int:pk>/comments/new/',
        views.CommentCreateView.as_view(),
        name='comment-create',
    ),
]
