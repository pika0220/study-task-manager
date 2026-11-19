"""URLの対応表の入口。各アプリの urls.py に振り分ける。"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # ユーザー登録（自分で作った部分）
    path("accounts/", include("accounts.urls")),
    # ログイン・ログアウトなど（Djangoに最初から用意されている部分）
    path("accounts/", include("django.contrib.auth.urls")),
    # タスクの画面
    path("", include("tasks.urls")),
]
