from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("tasks/new/", views.TaskCreateView.as_view(), name="task_create"),
    # <int:pk>：URLの中の数字をタスクの番号（pk）として受け取る。例：/tasks/5/toggle/
    path("tasks/<int:pk>/toggle/", views.task_toggle, name="task_toggle"),
]
