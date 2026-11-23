from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.views import LoginView
from django.shortcuts import render
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

    def dispatch(self, request, *args, **kwargs):
        # 本番で招待コードが決まっていないときは、新規登録を止める（フォームを出さず、送られても作らない）
        if not settings.INVITE_CODE and not settings.SIGNUP_WITHOUT_INVITE:
            return render(request, self.template_name, {"closed": True}, status=403)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        # 登録できたら、そのままログインした状態にしてホームへ移る
        response = super().form_valid(form)
        login(self.request, self.object)
        return response
