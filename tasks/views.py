from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DetailView, UpdateView

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


class OwnTaskMixin(LoginRequiredMixin):
    """ログインしている本人のタスクだけを扱うための共通部品。

    get_queryset で「自分のタスク」に絞っておくと、ほかの人のタスクの番号を
    URLに入れられても「見つからない（404）」になる。
    """

    def get_queryset(self):
        return self.request.user.tasks.all()


class TaskDetailView(OwnTaskMixin, DetailView):
    """タスクの詳細画面。内容と子タスクの一覧を見せる。"""

    template_name = "tasks/task_detail.html"

    def get_queryset(self):
        # 親（パンくず用）と、子タスク・孫タスク（進み具合用）をまとめて読み込む
        return super().get_queryset().select_related("parent").prefetch_related("children__children")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # 3段目のタスクには、もう子タスクを付けられない
        context["can_add_child"] = self.object.depth() < Task.MAX_DEPTH
        return context


class TaskCreateView(LoginRequiredMixin, CreateView):
    """タスクの登録画面。URLに親タスクの番号があれば、その子タスクとして登録する。

    LoginRequiredMixin：クラスで作るビュー用の @login_required。ログインしていなければログイン画面へ送る。
    CreateView：フォームを表示し、送られてきた内容が正しければ保存する、という流れをまとめた部品。
    """

    model = Task
    form_class = TaskForm
    template_name = "tasks/task_form.html"

    def dispatch(self, request, *args, **kwargs):
        # dispatch：GET でも POST でも最初に呼ばれる入り口
        self.parent = None
        if request.user.is_authenticated and "parent_pk" in kwargs:
            # 親にできるのは自分のタスクだけ
            self.parent = get_object_or_404(request.user.tasks, pk=kwargs["parent_pk"])
            if self.parent.depth() >= Task.MAX_DEPTH:
                messages.error(request, f"タスクの入れ子は{Task.MAX_DEPTH}段までです．")
                return redirect("task_detail", pk=self.parent.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        # 入力のチェック（モデルの clean：3段までなど）の前に、持ち主と親を決めておく
        form.instance.owner = self.request.user
        form.instance.parent = self.parent
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["parent"] = self.parent
        context["page_title"] = "子タスクの追加" if self.parent else "タスクの登録"
        context["submit_label"] = "追加する" if self.parent else "登録する"
        context["cancel_url"] = reverse("task_detail", args=[self.parent.pk]) if self.parent else reverse("home")
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        # 次の画面で一度だけ表示するお知らせ
        messages.success(self.request, f"「{self.object.title}」を登録しました．")
        return response

    def get_success_url(self):
        # 子タスクなら親の詳細画面へ、そうでなければホームへ戻る
        if self.parent:
            return reverse("task_detail", args=[self.parent.pk])
        return reverse("home")


class TaskUpdateView(OwnTaskMixin, UpdateView):
    """タスクの編集画面。登録画面と同じフォームと画面を使い回す。"""

    form_class = TaskForm
    template_name = "tasks/task_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["parent"] = self.object.parent
        context["page_title"] = "タスクの編集"
        context["submit_label"] = "保存する"
        context["cancel_url"] = reverse("task_detail", args=[self.object.pk])
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"「{self.object.title}」を保存しました．")
        return response

    def get_success_url(self):
        return reverse("task_detail", args=[self.object.pk])


@login_required
@require_POST  # データを変える操作なので POST だけ受け付ける（GET で来たら 405 を返す）
def task_toggle(request, pk):
    """タスクの完了・未完了を切り替えて、元の画面に戻る。"""
    # owner=request.user を条件に入れて、自分のタスクだけを探す。
    # ほかの人のタスクの番号を送られても「見つからない（404）」になる
    task = get_object_or_404(Task, pk=pk, owner=request.user)
    task.is_done = not task.is_done
    task.save()  # 完了日時（done_at）は Task.save() の中で自動で入る・消える
    if task.is_done:
        messages.success(request, f"「{task.title}」を完了にしました．")
    else:
        messages.info(request, f"「{task.title}」を未完了に戻しました．")
    # 押した画面（ホームや詳細画面）に戻る。
    # ただし、このサイトの中のURLのときだけ（ほかのサイトへ飛ばされるのを防ぐ）
    next_url = request.POST.get("next")
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect("home")
