from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required  # ログインしていない人はログイン画面に送られる
def home(request):
    """ホーム画面。自分のタスクを、未完了と完了に分けてカードで並べる。"""
    # 一番上の段（親のないタスク）だけを並べる。子タスクはカードの中で進み具合として見せる。
    # prefetch_related：子タスクをまとめて1回で読み込み、カードごとにデータベースへ聞きに行かないようにする
    tasks = request.user.tasks.filter(parent=None).prefetch_related("children")
    # 並び順はモデルの Meta.ordering（未完了 → 重要度が高い → 締切が近い）のまま
    open_tasks = [task for task in tasks if not task.is_done]
    done_tasks = [task for task in tasks if task.is_done]
    # 見出しの「未完了 ○件」は、並んでいるカードの数とそろえる（子タスクは数えない）
    open_count = len(open_tasks)
    return render(
        request,
        "tasks/home.html",
        {"open_tasks": open_tasks, "done_tasks": done_tasks, "open_count": open_count},
    )
