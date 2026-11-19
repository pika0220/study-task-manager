from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.urls import reverse_lazy
from django.views.generic import CreateView


class SignUpView(CreateView):
    """ユーザー登録の画面。登録フォームはDjangoに用意されている UserCreationForm を使う。"""

    form_class = UserCreationForm
    template_name = "registration/signup.html"
    success_url = reverse_lazy("home")

    def form_valid(self, form):
        # 登録できたら、そのままログインした状態にしてホームへ移る
        response = super().form_valid(form)
        login(self.request, self.object)
        return response
