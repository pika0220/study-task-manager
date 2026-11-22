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
    # ドラッグ＆ドロップでつなぐ：選べるつながりを調べる（GET）・保存する（POST）
    path("tasks/<int:pk>/link/options/", views.task_link_options, name="task_link_options"),
    path("tasks/<int:pk>/link/", views.task_link, name="task_link"),
    # マインドマップ（Issue #30）：画面・点の置き場所の保存・名前だけで追加・つながりを外す／切り替える
    path("map/", views.task_map, name="task_map"),
    # 5番のメインタスクのマップ：/map/5/
    path("map/<int:pk>/", views.task_map, name="task_map_root"),
    path("map/<int:pk>/arrange/", views.task_map_arrange, name="task_map_arrange"),
    path("map/tasks/new/", views.task_map_create, name="task_map_create"),
    path("tasks/<int:pk>/position/", views.task_map_position, name="task_map_position"),
    path("tasks/<int:pk>/unlink/", views.task_unlink, name="task_unlink"),
    path("tasks/<int:pk>/relink/", views.task_relink, name="task_relink"),
    # 資料（プログラムの中の名前は source）
    path("tasks/<int:pk>/sources/new/", views.source_create, name="source_create"),
    path("sources/<int:pk>/delete/", views.source_delete, name="source_delete"),
    path("sources/<int:pk>/photo/", views.source_photo, name="source_photo"),
    # 分類の管理
    path("categories/", views.category_list, name="category_list"),
    path("categories/new/", views.category_create, name="category_create"),
    path("categories/<int:pk>/edit/", views.category_update, name="category_update"),
    path("categories/<int:pk>/delete/", views.category_delete, name="category_delete"),
]
