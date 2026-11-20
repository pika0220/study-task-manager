from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("tasks/new/", views.TaskCreateView.as_view(), name="task_create"),
    # <int:pk>：URLの中の数字をタスクの番号（pk）として受け取る。例：/tasks/5/
    path("tasks/<int:pk>/", views.TaskDetailView.as_view(), name="task_detail"),
    path("tasks/<int:pk>/edit/", views.TaskUpdateView.as_view(), name="task_update"),
    path("tasks/<int:pk>/toggle/", views.task_toggle, name="task_toggle"),
    # 5番のタスクの下に子タスクを作る：/tasks/5/children/new/
    path("tasks/<int:parent_pk>/children/new/", views.TaskCreateView.as_view(), name="task_create_child"),
    # 指示の出どころ
    path("tasks/<int:pk>/sources/new/", views.source_create, name="source_create"),
    path("sources/<int:pk>/delete/", views.source_delete, name="source_delete"),
    # 分類の管理
    path("categories/", views.category_list, name="category_list"),
    path("categories/new/", views.category_create, name="category_create"),
    path("categories/<int:pk>/edit/", views.category_update, name="category_update"),
    path("categories/<int:pk>/delete/", views.category_delete, name="category_delete"),
]
