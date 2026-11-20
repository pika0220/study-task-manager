from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST
from django.views.generic import CreateView

from .forms import TaskForm
from .models import Task


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


class TaskCreateView(LoginRequiredMixin, CreateView):
    """タスクの登録画面。

    LoginRequiredMixin：クラスで作るビュー用の @login_required。ログインしていなければログイン画面へ送る。
    CreateView：フォームを表示し、送られてきた内容が正しければ保存する、という流れをまとめた部品。
    """

    model = Task
    form_class = TaskForm
    template_name = "tasks/task_form.html"
    success_url = reverse_lazy("home")

    def form_valid(self, form):
        # 入力が正しかったときに呼ばれる。保存する前に、持ち主をログイン中の本人にする
        form.instance.owner = self.request.user
        response = super().form_valid(form)
        # 次の画面（ホーム）で一度だけ表示するお知らせ
        messages.success(self.request, f"「{self.object.title}」を登録しました．")
        return response


@login_required
@require_POST  # データを変える操作なので POST だけ受け付ける（GET で来たら 405 を返す）
def task_toggle(request, pk):
    """タスクの完了・未完了を切り替えて、ホームに戻る。"""
    # owner=request.user を条件に入れて、自分のタスクだけを探す。
    # ほかの人のタスクの番号を送られても「見つからない（404）」になる
    task = get_object_or_404(Task, pk=pk, owner=request.user)
    task.is_done = not task.is_done
    task.save()  # 完了日時（done_at）は Task.save() の中で自動で入る・消える
    if task.is_done:
        messages.success(request, f"「{task.title}」を完了にしました．")
    else:
        messages.info(request, f"「{task.title}」を未完了に戻しました．")
    return redirect("home")
