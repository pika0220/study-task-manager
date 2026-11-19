from django.contrib.auth import login
from django.contrib.auth.views import LoginView
from django.urls import reverse_lazy
from django.views.generic import CreateView

from .forms import LoginForm, SignUpForm


class StyledLoginView(LoginView):
    """ログインの画面。仕組みはDjangoの LoginView のままで、フォームだけ見た目付きのものに替える。"""

    authentication_form = LoginForm


class SignUpView(CreateView):
    """ユーザー登録の画面。Djangoの UserCreationForm に見た目の class を付けた SignUpForm を使う。"""

    form_class = SignUpForm
    template_name = "registration/signup.html"
    success_url = reverse_lazy("home")

    def form_valid(self, form):
        # 登録できたら、そのままログインした状態にしてホームへ移る
        response = super().form_valid(form)
        login(self.request, self.object)
        return response
