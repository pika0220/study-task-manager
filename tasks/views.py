from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required  # ログインしていない人はログイン画面に送られる
def home(request):
    """ホーム画面（仮）。今はログインした人の名前と、未完了のタスクの数だけを出す。"""
    open_count = request.user.tasks.filter(is_done=False).count()
    return render(request, "tasks/home.html", {"open_count": open_count})
