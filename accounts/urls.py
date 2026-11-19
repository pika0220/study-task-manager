from django.urls import path

from . import views

urlpatterns = [
    # config/urls.py では、この表が django.contrib.auth.urls より先に読まれる。
    # URLは上から順に探して最初に合ったものを使うので、login/ はこちらの画面になる。
    path("login/", views.StyledLoginView.as_view(), name="login"),
    path("signup/", views.SignUpView.as_view(), name="signup"),
]
