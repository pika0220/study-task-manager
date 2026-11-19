from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("tasks/new/", views.TaskCreateView.as_view(), name="task_create"),
]
