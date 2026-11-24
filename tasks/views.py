import math
import re
from collections import defaultdict
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DetailView, UpdateView

from .forms import FeedbackForm, SourceForm, TaskForm
from .models import Category, Feedback, Source, Task, category_named


# ---- 戻り先（Issue #41）----
# 画面を移るときに「どこから来たか」を ?back=/map/room/4/ のようにアドレスに付けて運ぶ。
# 詳細画面の「←」は back があればそこへ戻る。編集・サブタスクの追加・資料のあとも back を付けたまま戻るので、
# マップから開いたタスクを編集しても、← でマップに戻れる。

# back にできる画面と、「←」に出す名前。ここにない画面のアドレスは使わない
BACK_LABELS = {
    "home": "ホーム",  # back がないときの「← ホーム」と同じ書き方
    "task_map_room": "🗺️ マップ",
    "progress": "📊 進み具合",
}


def _back_path(request):
    """GET か POST の back を読み、BACK_LABELS の画面のアドレス（/map/room/4/ など）なら返す。それ以外は None。

    ほかのサイトのアドレス（https://… や //…）や、知らない画面のアドレスを入れられても使わない
    （「戻る」を押したら別のサイトに飛ばされる、ということを防ぐ）。
    """
    value = request.POST.get("back") or request.GET.get("back")
    if not value or not value.startswith("/") or value.startswith("//"):
        return None
    if not url_has_allowed_host_and_scheme(value, allowed_hosts={request.get_host()}):
        return None
    path = urlsplit(value).path  # ?focus= などのクエリは使わない
    try:
        match = resolve(path)
    except Resolver404:
        return None
    return path if match.url_name in BACK_LABELS else None


def _back_query(request):
    """リンクの後ろに付ける「?back=…」（back がなければ空の文字）。"""
    path = _back_path(request)
    return "?" + urlencode({"back": path}) if path else ""


def _with_back(request, url):
    """url に、今の back を付けて返す。戻ったあとの画面でも、同じ戻り先を覚えておくため。"""
    return url + _back_query(request)


def _back_context(request, focus=None):
    """テンプレートに渡す戻り先。back：「←」のリンク {url, label}（なければ None）。

    マップに戻るときは ?focus=番号 を付けて、そのタスクの点を光らせる（task_map.js が読む）。
    """
    path = _back_path(request)
    back = None
    if path:
        name = resolve(path).url_name
        url = path
        if name == "task_map_room" and focus is not None:
            url += "?" + urlencode({"focus": focus})
        back = {"url": url, "label": BACK_LABELS[name]}
    return {"back": back, "back_path": path or "", "back_query": _back_query(request)}


# ---- 画面の一部だけ書きかえる（htmx・Issue #57）----
# htmx から来た操作には、ページ全体ではなく、変わった部分の HTML だけを返す。
# 書きかえ先は id で決まっている：card-○（カード）・row-○（サブタスクの行）・summary-○（▶ の行）・
# detail-○（詳細の上の欄）・open-count / done-count（ホームの件数）。○ はタスクの番号。
# どの部分が今の画面にあるかは、ブラウザ（static/js/app.js）が on_page で教えてくれる。


def _is_htmx(request):
    """htmx から送られてきた操作か（htmx は HX-Request: true という印を付けて送る）。"""
    return request.headers.get("HX-Request") == "true"


def _on_page(request):
    """今の画面にある書きかえ先の id の集まり（例：{"card-5", "row-7", "open-count"}）。"""
    return {part for part in request.POST.get("on_page", "").split(",") if part}


def _render_part(request, template, context, oob=False):
    """部分の HTML を作る。oob=True のときは、ほかの場所（id が同じ部分）も一緒に書きかえる印を付ける。

    hx-swap-oob：htmx の「メインの場所とは別に、同じ id の部分も書きかえて」という印。
    いちばん外側の要素にだけ付ける（中の要素に付けると、そこもばらばらに書きかえられてしまうため）。
    """
    html = render_to_string(template, {**_back_context(request), **context}, request)
    if oob:
        html = re.sub(r"<(article|li|summary|div|span)\b", r'<\1 hx-swap-oob="true"', html, count=1)
    return html


def _task_for_part(user, pk):
    """部分を作り直すためのタスク。カードや行に出すもの（サブタスクの数・資料の数など）をまとめて読む。"""
    return (
        user.tasks.select_related("category", "prerequisite", "parent")
        .prefetch_related("children", "sources")
        .get(pk=pk)
    )


def _task_part(request, task, part_id, oob=False):
    """id（card-○・row-○・detail-○）に合わせて、そのタスクの部分を作る。"""
    kind = part_id.split("-")[0]
    task = _task_for_part(request.user, task.pk)
    if kind == "card":
        return _render_part(request, "tasks/_task_card.html", {"task": task, "card_level": task.depth()}, oob)
    if kind == "row":
        return _render_part(request, "tasks/_child_row.html", {"task": task, "level": task.depth()}, oob)
    return _render_part(request, "tasks/_detail_summary.html", {"task": task}, oob)


@login_required  # ログインしていない人はログイン画面に送られる
def home(request):
    """ホーム画面。自分のタスクを、未完了と完了に分けてカードで並べる。"""
    # 一番上の段（親のないタスク）だけを並べる。サブタスクはカードの中で開いて見せる。
    # prefetch_related：子・孫・ひ孫をまとめて読み込み、カードごとにデータベースへ聞きに行かないようにする
    # select_related("category")：カードに出す分類の名前も、同じ問い合わせで一緒に読む
    tasks = (
        request.user.tasks.filter(parent=None)
        .select_related("category", "prerequisite")
        # サブタスクの中身は ▶ を押したときに読む（task_children）。ここでは数と進み具合のために1段目だけ
        .prefetch_related("children", "sources")
    )
    # 並び順はモデルの Meta.ordering（未完了 → 重要度が高い → 締切が近い）のまま
    open_tasks = [task for task in tasks if not task.is_done]
    # 今すぐ取りかかれるタスクを上に、🔒（先にやることが終わっていない）を下に。
    # sorted は「同じ順位のものは元の順番を保つ」ので、それぞれの中は重要度 → 締切の順のまま
    open_tasks = sorted(open_tasks, key=lambda task: task.is_locked)
    done_tasks = [task for task in tasks if task.is_done]
    # 見出しの「未完了 ○件」は、並んでいるカードの数とそろえる（サブタスクは数えない）
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
    """タスクの詳細画面。内容とサブタスクの一覧を見せる。"""

    template_name = "tasks/task_detail.html"

    def get_queryset(self):
        # 親（パンくず用）と、サブタスク（3段目まで）（カードの中で開く用）をまとめて読み込む
        return (
            super()
            .get_queryset()
            .select_related("parent", "category", "prerequisite")
            .prefetch_related(
                # サブタスクのカードの中身（孫）は、▶ を押したときに読む。ここでは数と進み具合のために孫まで
                "children__children",
                "children__category",
                "children__sources",
                "children__prerequisite",
                "sources",
                "dependents",
                "related",
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        depth = self.object.depth()
        # 3段目のタスクには、もうサブタスクを付けられない
        context["can_add_child"] = depth < Task.MAX_DEPTH
        # この画面に並ぶサブタスクのカードが何段目か（カードの中の「＋ サブタスクを追加」を出すかどうかに使う）
        context["child_level"] = depth + 1
        # 資料を足すフォーム（空の状態）
        context["source_form"] = SourceForm()
        # 戻り先（マップ・進み具合から来たときは、「←」をそこにする）
        context.update(_back_context(self.request, focus=self.object.pk))
        return context


class TaskCreateView(LoginRequiredMixin, CreateView):
    """タスクの登録画面。URLに親タスクの番号があれば、そのサブタスクとして登録する。

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

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user  # 親タスクの選択肢を「自分のタスク」で作るため
        return kwargs

    def get_initial(self):
        # 詳細画面などから来たときは、親タスクの欄に最初からそのタスクを入れておく
        initial = super().get_initial()
        if self.parent:
            initial["parent"] = self.parent.pk
        return initial

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        # 入力のチェック（モデルの clean：3段までなど）の前に、持ち主を決めておく
        form.instance.owner = self.request.user
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = "サブタスクの追加" if self.parent else "タスクの登録"
        context["submit_label"] = "追加する" if self.parent else "登録する"
        context["cancel_url"] = (
            _with_back(self.request, reverse("task_detail", args=[self.parent.pk])) if self.parent else reverse("home")
        )
        context.update(_back_context(self.request))
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        # 次の画面で一度だけ表示するお知らせ
        messages.success(self.request, f"「{self.object.title}」を登録しました．")
        return response

    def get_success_url(self):
        # サブタスクなら親の詳細画面へ（戻り先を付けたまま）、そうでなければホームへ戻る
        if self.parent:
            return _with_back(self.request, reverse("task_detail", args=[self.parent.pk]))
        return reverse("home")


class TaskUpdateView(OwnTaskMixin, UpdateView):
    """タスクの編集画面。登録画面と同じフォームと画面を使い回す。"""

    form_class = TaskForm
    template_name = "tasks/task_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user  # 親タスクの選択肢を「自分のタスク」で作るため
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = "タスクの編集"
        context["submit_label"] = "保存する"
        context["cancel_url"] = _with_back(self.request, reverse("task_detail", args=[self.object.pk]))
        context.update(_back_context(self.request))
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"「{self.object.title}」を保存しました．")
        return response

    def get_success_url(self):
        # 詳細画面に戻る。戻り先（マップなど）を付けたままにして、そこから ← で戻れるようにする
        return _with_back(self.request, reverse("task_detail", args=[self.object.pk]))


@login_required
@require_POST  # データを変える操作なので POST だけ受け付ける（GET で来たら 405 を返す）
def task_toggle(request, pk):
    """タスクの完了・未完了を切り替えて、元の画面に戻る。"""
    # owner=request.user を条件に入れて、自分のタスクだけを探す。
    # ほかの人のタスクの番号を送られても「見つからない（404）」になる
    task = get_object_or_404(Task, pk=pk, owner=request.user)
    task.is_done = not task.is_done
    task.save()  # 完了日時（done_at）は Task.save() の中で自動で入る・消える
    if _is_htmx(request):
        return _toggle_parts(request, task)
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


def _toggle_parts(request, task):
    """○ を押したあと（htmx）に返す部分。ページを読み直さずに、変わったところだけを書きかえる。

    - メイン：押したタスク自身（カード・行・詳細の上の欄のうち、画面にあるもの）
    - ついでに書きかえるもの（oob）：
      ・上のタスクの ▶ の行（進み具合の ％ が変わる）
      ・このタスクが終わってからやるタスク（🔒 が外れる・付く）
      ・ホームの「未完了 ○件」「完了したタスク（○件）」
    お知らせ（messages）は出さない。見た目がその場で変わるので十分なため
    """
    on_page = _on_page(request)
    own_ids = [f"{kind}-{task.pk}" for kind in ("card", "row", "detail")]
    main_id = next((part for part in own_ids if part in on_page), None)
    if main_id is None:
        raise Http404  # 画面に書きかえる場所がない（ふつうは起きない）
    html = [_task_part(request, task, main_id)]

    if task.parent_id and f"summary-{task.parent_id}" in on_page:
        parent = request.user.tasks.prefetch_related("children").get(pk=task.parent_id)
        html.append(_render_part(request, "tasks/_children_summary.html", {"parent": parent}, oob=True))
    for dependent in task.dependents.all():
        for part in (f"card-{dependent.pk}", f"row-{dependent.pk}"):
            if part in on_page:
                html.append(_task_part(request, dependent, part, oob=True))
    # 件数は中身（数字）だけを書きかえる（innerHTML）。要素ごと書きかえると、色や目印（data-swap-id）が消えるため
    tops = request.user.tasks.filter(parent=None)
    if "open-count" in on_page:
        html.append(f'<span id="open-count" hx-swap-oob="innerHTML">{tops.filter(is_done=False).count()}</span>')
    if "done-count" in on_page:
        html.append(f'<span id="done-count" hx-swap-oob="innerHTML">{tops.filter(is_done=True).count()}</span>')
    return HttpResponse("".join(html))


@login_required
def task_children(request, pk):
    """▶ を押したときに読みこむ、サブタスクの行（htmx・Issue #57）。

    前はホームを開くときに、全部のサブタスクを最初から HTML に入れていた（タスクが多いと重い）。
    """
    parent = get_object_or_404(
        request.user.tasks.prefetch_related("children__children", "children__prerequisite"), pk=pk
    )
    return render(request, "tasks/_children_list.html", {
        **_back_context(request), "parent": parent, "level": parent.depth(),
    })


# ---- 分類の管理（名前の変更・追加・削除）----

CATEGORY_NAME_MAX = 50


def _clean_category_name(request, name):
    """分類の名前を確かめる。だめならお知らせを出して None を返す。"""
    name = (name or "").strip()
    if not name:
        messages.error(request, "分類の名前を入れてください．")
        return None
    if len(name) > CATEGORY_NAME_MAX:
        messages.error(request, f"分類の名前は{CATEGORY_NAME_MAX}文字までです．")
        return None
    return name


@login_required
def category_list(request):
    """分類の一覧。名前の変更・追加・削除をこの画面で行う。"""
    # annotate(task_count=Count("tasks"))：分類ごとのタスクの数を、一緒に数えて持ってくる
    categories = request.user.categories.annotate(task_count=Count("tasks"))
    # マップの ⚙️ から来たときは、「←」をマップにする
    return render(request, "tasks/category_list.html", {"categories": categories, **_back_context(request)})


@login_required
@require_POST
def category_create(request):
    name = _clean_category_name(request, request.POST.get("name"))
    if name:
        # 名前は暗号化しているので、同じ名前があるかは Python の中でくらべる（category_named）
        if category_named(request.user, name):
            messages.info(request, f"分類「{name}」はもうあります．")
        else:
            request.user.categories.create(name=name)
            messages.success(request, f"分類「{name}」を作りました．")
    return redirect(_with_back(request, reverse("category_list")))  # 戻り先（マップなど）を付けたまま


@login_required
@require_POST
def category_update(request, pk):
    category = get_object_or_404(request.user.categories, pk=pk)
    name = _clean_category_name(request, request.POST.get("name"))
    if name and name != category.name:
        if category_named(request.user, name):
            # 同じ名前の分類がもうある（名前は暗号化しているので、Python の中でくらべる）
            messages.error(request, f"分類「{name}」はもうあります．")
        else:
            old = category.name
            category.name = name
            category.save()
            messages.success(request, f"「{old}」を「{name}」に変えました．")
    return redirect(_with_back(request, reverse("category_list")))  # 戻り先（マップなど）を付けたまま


@login_required
@require_POST
def category_delete(request, pk):
    category = get_object_or_404(request.user.categories, pk=pk)
    count = category.tasks.count()
    name = category.name
    category.delete()  # この分類のタスクは消えずに「分類なし」になる（on_delete=SET_NULL）
    if count:
        messages.success(request, f"分類「{name}」を消しました．この分類だった{count}件のタスクは「分類なし」になりました．")
    else:
        messages.success(request, f"分類「{name}」を消しました．")
    return redirect(_with_back(request, reverse("category_list")))  # 戻り先（マップなど）を付けたまま


# ---- 資料（F-04。最初は「出どころ」という名前だった）----


@login_required
@require_POST
def source_create(request, pk):
    """タスクに資料を足して、詳細画面に戻る。"""
    task = get_object_or_404(request.user.tasks, pk=pk)  # 自分のタスクにだけ足せる
    # request.FILES：フォームで送られたファイル（写真）はここに入る
    form = SourceForm(request.POST, request.FILES, instance=Source(task=task), owner=request.user)
    if form.is_valid():
        form.save()
        messages.success(request, "資料を追加しました．")
    else:
        # 入力のまちがいを、お知らせとして出す（例：リンク・メモ・写真のどれか1つ以上を入れてください）
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
    return redirect(_with_back(request, reverse("task_detail", args=[task.pk])))


@login_required
@require_POST
def source_delete(request, pk):
    """資料を消して、詳細画面に戻る。写真のファイルはシグナル（signals.py）で一緒に消える。"""
    # task__owner：資料 → タスク → 持ち主、とたどって、自分のものだけを探す
    source = get_object_or_404(Source, pk=pk, task__owner=request.user)
    task_pk = source.task_id
    source.delete()
    if _is_htmx(request):
        # 資料の一覧だけを書きかえ、見出しの件数も一緒に直す（Issue #57）
        task = request.user.tasks.prefetch_related("sources").get(pk=task_pk)
        html = _render_part(request, "tasks/_sources_list.html", {"task": task})
        html += f'<span id="sources-count" hx-swap-oob="innerHTML">{task.sources.count()}</span>'
        return HttpResponse(html)
    messages.success(request, "資料を消しました．")
    return redirect(_with_back(request, reverse("task_detail", args=[task_pk])))


@login_required
def source_photo(request, pk):
    """資料の写真を返す。持ち主だけが見られる（ほかの人は404）。

    写真のフォルダは公開していないので、写真を見るにはこの画面を通るしかない。
    """
    source = get_object_or_404(Source, pk=pk, task__owner=request.user)
    if not source.photo:
        raise Http404
    response = FileResponse(source.photo.open("rb"), content_type="image/jpeg")
    # private：ブラウザ以外（途中のサーバーなど）に写真を保存させない
    response["Cache-Control"] = "private, max-age=3600"
    return response



# ---- ドラッグ＆ドロップでつなぐ（Issue #29）----

# relation の値 → 書きかえるタスクの欄
LINK_FIELDS = {"child": "parent", "after": "prerequisite"}
# 選べるつなぎ方の全部。"related"（🔗 関連・Issue #43）は欄ではなく、多対多の表に入れる
RELATIONS = [*LINK_FIELDS, "related"]


def _check_link(source, target, relation):
    """source を target に「サブタスクにする（child）」「終わってからやる（after）」ができるか確かめる。

    実際には保存せず、モデルの clean()（3段まで・輪にならない など）を通して確かめる。
    ルールを1か所（モデル）にまとめておくと、フォームからでもドラッグからでも同じチェックになる。
    戻り値：(できるか, できないときの理由)
    """
    if source.pk == target.pk:
        return False, "同じタスクです．"
    if relation == "related":
        reason = source.related_problem(target)
        return reason is None, reason or ""
    field = LINK_FIELDS[relation]
    candidate = Task.objects.get(pk=source.pk)  # 保存しない「下書き」として読み直す
    setattr(candidate, field, target)
    try:
        candidate.full_clean()
    except ValidationError as error:
        messages_for_field = error.message_dict.get(field) or error.messages
        return False, messages_for_field[0]
    return True, ""


def _own_task_from_param(request, value):
    """フォームやURLから来た番号で、自分のタスクを探す。数字でなければ404。"""
    try:
        pk = int(value)
    except (TypeError, ValueError):
        raise Http404
    return get_object_or_404(request.user.tasks, pk=pk)


def _link_notes(source, target, relation):
    """つないだら何が変わるかを、短い記号の形で返す（選ぶ画面に出す）。

    - 今あるつながりを入れかえるとき：「⚠ 今：「A」のサブタスク → 入れかえ」
    - サブタスクにすると分類が変わるとき：「分類：授業 → 研究（サブタスクもそろう）」
    """
    notes = []
    # 🔗 でつながっている相手と 📁・🔒 でつなぐと、🔗 は外れる（線は1本だけ）
    if relation != "related" and source.related.filter(pk=target.pk).exists():
        notes.append("🔗 → 外れる（線は1本だけ）")
    if relation == "related":
        return notes
    if relation == "child":
        if source.parent_id and source.parent_id != target.pk:
            notes.append(f"⚠ 今：「{source.parent.title}」のサブタスク → 入れかえ")
        # 分類はいちばん上のタスクにそろっているので、相手の分類が、入れたあとの分類になる
        if source.category_id != target.category_id:
            before = source.category.name if source.category else "分類なし"
            after = target.category.name if target.category else "分類なし"
            notes.append(f"分類：{before} → {after}（サブタスクもそろう）")
    else:
        if source.prerequisite_id and source.prerequisite_id != target.pk:
            notes.append(f"⚠ 今：「{source.prerequisite.title}」が終わってから → 入れかえ")
    return notes


@login_required
def task_link_options(request, pk):
    """カードを落としたときに、選べるつながりと、選べない理由・変わることを JSON で返す。"""
    source = get_object_or_404(request.user.tasks.select_related("parent", "prerequisite", "category"), pk=pk)
    target = _own_task_from_param(request, request.GET.get("target"))
    result = {"source": source.title, "target": target.title}
    for relation in RELATIONS:
        ok, reason = _check_link(source, target, relation)
        result[relation] = {"ok": ok, "reason": reason, "notes": _link_notes(source, target, relation) if ok else []}
    return JsonResponse(result)


@login_required
@require_POST
def task_link(request, pk):
    """選んだつながりを保存して、元の画面に戻る。"""
    source = get_object_or_404(request.user.tasks, pk=pk)
    target = _own_task_from_param(request, request.POST.get("target"))
    relation = request.POST.get("relation")
    if relation not in RELATIONS:
        messages.error(request, "つなぎ方を選んでください．")
    else:
        ok, reason = _check_link(source, target, relation)
        if not ok:
            messages.error(request, reason)
        elif relation == "related":
            source.related.add(target)  # 向きがないので、target から見ても source とつながる
            messages.success(request, f"「{source.title}」と「{target.title}」を 🔗 でつなぎました．")
        else:
            field = LINK_FIELDS[relation]
            setattr(source, field, target)
            source.save(update_fields=[field, "updated_at"])
            if relation == "child":
                messages.success(request, f"「{source.title}」を「{target.title}」のサブタスクにしました．")
            else:
                messages.success(request, f"「{source.title}」は「{target.title}」が終わってからにしました．")
    return _redirect_next(request, "home")


def _redirect_next(request, default):
    """フォームの next（元いた画面）に戻る。ほかのサイトのURLは使わず default に戻す。"""
    next_url = request.POST.get("next")
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect(default)


# ---- マインドマップ（Issue #30・#36）----
# 分類ごとに1つの「部屋」（マップ）がある。部屋には、その分類のいちばん上のタスクを「棚」から選んで置く。
# 置いたタスクは、サブタスク（子・孫）と一緒に、木の形で外へ広がる。真ん中には何も置かない。

MAP_LIMIT = 5000  # 置ける場所の上限（px）。ありえない大きな数字を保存しないため
MAP_CENTER = 800  # 最初に置くタスクの場所（点の中心）。まわりに広げられるよう、紙の真ん中あたりにする
MAP_RADIUS = 200  # 自動で並べるとき、1段外へ広がるごとに離す距離
MAP_TREE_GAP = 80  # 新しい木を、今ある点の下に置くときの、縦のすきま（px）
MAP_NODE_WIDTH = 130  # 点の大きさ（task_map.html の .map-node と同じ）
MAP_NODE_HEIGHT = 44
NO_CATEGORY = "none"  # 「分類なし」の部屋の URL での名前（/map/room/none/）


def _map_tree(top, children):
    """いちばん上のタスクと、その下の未完了のタスク（子・孫）を、上から順に並べたリストを返す。"""
    result = [top]
    for child in children[top.pk]:
        if not child.is_done:
            result += _map_tree(child, children)
    return result


def _map_layout_children(shown):
    """マップの上で「どの点から伸ばすか」を決める。{伸ばす元の番号: [タスク, …]} と、前後でつないだタスクの番号の集合を返す。

    ふつうは親から伸ばす（親がいなければ、自分がひとつの木のいちばん上：キーは None）。
    ただし、先にやることが「同じ親の中のタスク（きょうだい）」のときは、先にやることの先に伸ばす
    （卒業研究 → 文献調査 → 考察を書く のように、順番が一列に見える）。いちばん上のタスクどうしでも同じ。
    データ（親子・前後）は変えず、マップでの見せ方だけを変える。
    """
    by_pk = {t.pk: t for t in shown}
    layout_children = defaultdict(list)
    chained = set()
    for task in shown:
        before = by_pk.get(task.prerequisite_id)
        if before is not None and before.parent_id == task.parent_id:
            layout_children[before.pk].append(task)
            chained.add(task.pk)
        elif task.parent_id in by_pk:
            layout_children[task.parent_id].append(task)
        else:
            layout_children[None].append(task)
    return layout_children, chained


def _radial_layout(shown, children):
    """マップの上での置き場所（点の左上）を {タスクの番号: (x, y)} で返す（保存はしない）。

    children：どの点から伸ばすか（_map_layout_children で決めたもの）。children[None] が木のいちばん上。

    1. 置き場所が保存されている点は、その場所のまま。
    2. まだ決まっていない木のいちばん上は、今ある点のいちばん下より、さらに下に置く（縦に積む）。
    3. サブタスクは「理想の場所」（元の点のまわり 360度 を分け合い、その先は外向きに同じ距離ずつ）を基本に、
       ほかの点と重なるときは、元の点のまわりを少しずつ回して、空いている場所を探す。
    """
    shown_ids = {t.pk for t in shown}

    def kids(task):
        return [c for c in children[task.pk] if c.pk in shown_ids]

    def leaves(task):
        """枝の先（子のないタスク）の数。広げる角度の割合に使う。"""
        branch = kids(task)
        return sum(leaves(k) for k in branch) if branch else 1

    centers = {
        t.pk: (t.map_x + MAP_NODE_WIDTH / 2, t.map_y + MAP_NODE_HEIGHT / 2)
        for t in shown
        if t.map_x is not None and t.map_y is not None
    }

    def overlaps(point, gap_x=MAP_NODE_WIDTH + 12, gap_y=MAP_NODE_HEIGHT + 12):
        x, y = point
        return any(abs(x - cx) < gap_x and abs(y - cy) < gap_y for cx, cy in centers.values())

    def free_top_spot(top_pks, rise):
        """木のいちばん上を置く場所。スマホの縦長の画面に合うよう、今ある点の「下」に縦に積む。

        top_pks：木のいちばん上の番号。横の位置は、その中で場所が決まっている、いちばん下の木にそろえる。
        rise：新しい木が、いちばん上のタスクより上に広がる高さ（サブタスクが上に出る分）。
        """
        if not centers:
            return (MAP_CENTER, MAP_CENTER)  # 部屋で最初の木は、紙の真ん中あたり
        lowest_top = max((centers[pk] for pk in top_pks if pk in centers), key=lambda c: c[1], default=None)
        x = lowest_top[0] if lowest_top else MAP_CENTER
        bottom = max(cy for _, cy in centers.values()) + MAP_NODE_HEIGHT / 2  # 今ある点の、いちばん下のふち
        return (x, bottom + MAP_TREE_GAP + MAP_NODE_HEIGHT / 2 + rise)

    def rise_of(top, offset_of):
        """木のいちばん上から見て、サブタスクがどれだけ上に出るか（px）。理想の場所で数える。"""
        highest = 0

        def walk(task, y):
            nonlocal highest
            for k in kids(task):
                ky = y + offset_of[k.pk][1]
                highest = min(highest, ky)
                walk(k, ky)

        walk(top, 0)
        return -highest

    def free_spot(parent_center, offset):
        """元の点から offset だけ離れた場所を基本に、重ならない場所を探す。"""
        px, py = parent_center
        dx, dy = offset
        for ring in range(4):  # 1周しても見つからなければ、少し遠くでもう1周
            stretch = 1 + 0.5 * ring
            for step in range(12):  # 30度ずつ、右回り・左回りに交互に回す（0, +30, -30, +60, …）
                turn = math.radians(30 * ((step + 1) // 2) * (1 if step % 2 else -1))
                rx = (dx * math.cos(turn) - dy * math.sin(turn)) * stretch
                ry = (dx * math.sin(turn) + dy * math.cos(turn)) * stretch
                if not overlaps((px + rx, py + ry)):
                    return (px + rx, py + ry)
        return (px + dx, py + dy)  # どうしても空きがなければ、重なってもよいので基本の場所に置く

    def offsets(task, level, start, end, result):
        """task から見た、サブタスクの理想のずれ {番号: (dx, dy)} を作る（元の点からの相対位置）。"""
        if level > 0:
            # 木のいちばん上より外は、元の点から見て外向き ±60度 の中だけに広げる
            middle = (start + end) / 2
            half = min((end - start) / 2, math.pi / 3)
            start, end = middle - half, middle + half
        branch = kids(task)
        total = sum(leaves(k) for k in branch)
        angle = start
        for k in branch:
            span = (end - start) * leaves(k) / total
            direction = angle + span / 2
            result[k.pk] = (MAP_RADIUS * math.cos(direction), MAP_RADIUS * math.sin(direction))
            offsets(k, level + 1, angle, angle + span, result)
            angle += span

    def place(task, offset_of):
        for k in kids(task):
            if k.pk not in centers:
                centers[k.pk] = free_spot(centers[task.pk], offset_of[k.pk])
            place(k, offset_of)

    root_pks = [t.pk for t in children[None]]
    for top in children[None]:
        if top.pk not in shown_ids:
            continue
        offset_of = {}
        # 左（-180度）から1周。サブタスクが1つなら右、2つなら上と下に出る（スマホの縦長の画面に合う）
        offsets(top, 0, -math.pi, math.pi, offset_of)
        if top.pk not in centers:
            centers[top.pk] = free_top_spot(root_pks, rise_of(top, offset_of))
        place(top, offset_of)

    return {
        pk: (min(max(cx - MAP_NODE_WIDTH / 2, 0), MAP_LIMIT), min(max(cy - MAP_NODE_HEIGHT / 2, 0), MAP_LIMIT))
        for pk, (cx, cy) in centers.items()
    }


def _room_key(category_id):
    """分類の番号から、部屋の URL での名前を作る（分類なしは "none"）。"""
    return str(category_id) if category_id else NO_CATEGORY


def _room_redirect(task, focus=False):
    """そのタスクがいる部屋（いちばん上のタスクの分類の部屋）へ移る。

    focus=True：置いた・足したタスクを、画面の見える場所に出す（?focus=番号 を付ける。task_map.js が読む）。
    付けないと、前に見ていた場所に戻るので、離れた場所に置かれたタスクが画面の外になってしまう。
    """
    url = reverse("task_map_room", kwargs={"key": _room_key(task.root().category_id)})
    if focus:
        url += f"?focus={task.pk}"
    return redirect(url)


@login_required
def task_map(request):
    """/map/ で開いたとき：タスクを置いてある部屋（なければ、タスクのある部屋・最初の分類）へ移る。"""
    tops = [t for t in request.user.tasks.filter(parent=None, is_done=False)]
    placed = [t for t in tops if t.on_map]
    first = (placed or tops or [None])[0]
    if first is not None:
        return redirect("task_map_room", key=_room_key(first.category_id))
    category = request.user.categories.first()
    if category is not None:
        return redirect("task_map_room", key=_room_key(category.pk))
    return redirect("task_map_room", key=NO_CATEGORY)


@login_required
def task_map_root(request, pk):
    """タスクの番号で開いたとき（進み具合の 🗺️ など）：そのタスクがいる部屋へ移る。"""
    return _room_redirect(get_object_or_404(request.user.tasks, pk=pk))


@login_required
def task_map_room(request, key):
    """分類ごとの部屋（マップ）。置いたタスクを点、つながりを線で見せる。点と線は task_map.js が描く。"""
    if key == NO_CATEGORY:
        category = None
    else:
        try:
            category = request.user.categories.get(pk=int(key))
        except (ValueError, Category.DoesNotExist):
            raise Http404
    category_id = category.pk if category else None

    # 自分のタスクを1回の問い合わせでまとめて読み、親子の形は Python の中で組み立てる
    # prefetch_related("related")：🔗 の相手を、全部のタスクの分まとめて1回で読む
    tasks = list(request.user.tasks.select_related("prerequisite").prefetch_related("related"))
    children = defaultdict(list)
    for task in tasks:
        if task.parent_id:
            children[task.parent_id].append(task)
    tops = [t for t in tasks if t.parent_id is None and not t.is_done]

    # 部屋の切り替えメニュー：自分の分類（作った順）と、いちばん上に分類なしのタスクがあれば「分類なし」
    counts = defaultdict(int)
    for t in tops:
        counts[t.category_id] += 1
    rooms = [
        {"key": _room_key(c.pk), "name": c.name, "count": counts[c.pk]}
        for c in request.user.categories.all()
    ]
    if counts[None] or category is None:
        rooms.append({"key": NO_CATEGORY, "name": "分類なし", "count": counts[None]})

    in_room = [t for t in tops if t.category_id == category_id]
    placed = [t for t in in_room if t.on_map]
    shelf = [t for t in in_room if not t.on_map]
    shown = []
    for top in placed:
        shown += _map_tree(top, children)

    layout_children, chained = _map_layout_children(shown)
    positions = _radial_layout(shown, layout_children)
    # 自動で決めた場所は、すぐに保存しておく。保存しないと、タスクを足したりつないだりするたびに
    # 並べ直されて、点が勝手に動いてしまうため。bulk_update：まとめて1回の問い合わせで保存する
    unplaced = [t for t in shown if t.map_x is None or t.map_y is None]
    for t in unplaced:
        t.map_x, t.map_y = positions[t.pk]
    if unplaced:
        Task.objects.bulk_update(unplaced, ["map_x", "map_y"])

    shown_ids = {t.pk for t in shown}
    # 「＋ タスク」で選べる、つなぐ先（この部屋のすべての点）。何段目か（level）も渡し、
    # 3段目の「中」には入れられないことを画面で示す。何段目かは、上から順に並んだ shown（親が必ず子より先）で数える
    levels = {}
    anchors = []
    for t in shown:
        level = 1 if t.parent_id is None else levels[t.parent_id] + 1
        levels[t.pk] = level
        label = ("　" * (level - 1)) + ("└ " if level > 1 else "") + t.title
        anchors.append({"pk": t.pk, "label": label, "level": level})
    nodes = [
        {
            "id": t.pk,
            "title": t.title,
            "x": positions[t.pk][0],
            "y": positions[t.pk][1],
            "importance": t.importance,
            "locked": t.is_locked and not t.is_done,
            "done": t.is_done,
            "top": t.parent_id is None,  # いちばん上のタスク（少し目立たせる）
            # 詳細画面の「←」でこの部屋に戻れるように、戻り先（back）を付けておく
            "url": reverse("task_detail", args=[t.pk]) + "?" + urlencode({"back": request.path}),
            # 線を引くのは、相手もこの部屋に出ているときだけ
            "parent": t.parent_id if t.parent_id in shown_ids else None,
            "prerequisite": t.prerequisite_id if t.prerequisite_id in shown_ids else None,
            # 🔗 の相手（この部屋に出ている分だけ）。違う部屋の相手は、詳細画面にだけ出る
            "related": [r.pk for r in t.related.all() if r.pk in shown_ids],
            # True：先にやること（同じ親の中のタスク）の先に描く。親との線は引かない
            "chained": t.pk in chained,
        }
        for t in shown
    ]
    return render(
        request,
        "tasks/task_map.html",
        {
            "nodes": nodes,
            "rooms": rooms,
            "room_key": _room_key(category_id),
            "room_name": category.name if category else "分類なし",
            "anchors": anchors,
            "placed": placed,
            "shelf": shelf,
        },
    )


def _map_coordinate(value):
    """フォームから来た位置を数字にする。数字でなければ None。はみ出す数字は 0～MAP_LIMIT におさめる。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):  # nan（数字でない）や inf（無限大）は受け付けない
        return None
    return min(max(number, 0), MAP_LIMIT)


@login_required
@require_POST
def task_map_position(request, pk):
    """マップで点を動かしたときに、置き場所を保存する（JavaScript から送られる）。"""
    task = get_object_or_404(request.user.tasks, pk=pk)
    x = _map_coordinate(request.POST.get("x"))
    y = _map_coordinate(request.POST.get("y"))
    if x is None or y is None:
        return JsonResponse({"ok": False}, status=400)
    task.map_x, task.map_y = x, y
    # 置き場所は中身の変更ではないので、更新日時（updated_at）は変えない
    task.save(update_fields=["map_x", "map_y"])
    return JsonResponse({"ok": True, "x": x, "y": y})


def _clear_positions(user, top):
    """いちばん上のタスクと、そのサブタスクの置き場所を消す（次に開いたときに、空いている場所へ自動で並べる）。"""
    user.tasks.filter(Q(pk=top.pk) | Q(parent=top) | Q(parent__parent=top)).update(map_x=None, map_y=None)


@login_required
@require_POST
def task_map_place(request, pk):
    """棚のタスクを部屋に置く。サブタスクも一緒に出る。"""
    top = get_object_or_404(request.user.tasks, pk=pk, parent=None)
    _clear_positions(request.user, top)  # 前に置いていた場所ではなく、今の部屋の空いている場所に並べる
    top.on_map = True
    top.save(update_fields=["on_map"])
    messages.success(request, f"📦 → 🗺️ 「{top.title}」を置きました．")
    return _room_redirect(top, focus=True)


@login_required
@require_POST
def task_map_stow(request, pk):
    """部屋に置いたタスクを棚にしまう。タスクそのものや、つながりは変えない。"""
    top = get_object_or_404(request.user.tasks, pk=pk, parent=None)
    top.on_map = False
    top.save(update_fields=["on_map"])
    messages.success(request, f"🗺️ → 📦 「{top.title}」を棚にしまいました．")
    return _room_redirect(top)


@login_required
@require_POST
def task_map_create(request):
    """部屋から、名前だけでタスクを足す。締切などはあとで詳細画面から入れる。

    anchor（どのタスクに）と relation（つなぎ方）で、足す場所を決める。
      - anchor なし：この部屋の分類で、いちばん上のタスクとして置く（room：部屋の名前）
      - relation="child"：anchor のサブタスクにする（anchor のサブタスク）
      - relation="after"：anchor が終わってからやる。anchor と同じ段（anchor の親の中）に置く
    """
    title = (request.POST.get("title") or "").strip()
    max_length = Task._meta.get_field("title").max_length
    anchor = None
    if request.POST.get("anchor"):
        anchor = _own_task_from_param(request, request.POST.get("anchor"))
    relation = request.POST.get("relation", "child")

    task = Task(
        owner=request.user,
        title=title,
        map_x=_map_coordinate(request.POST.get("x")),
        map_y=_map_coordinate(request.POST.get("y")),
    )
    error = None
    if not title:
        error = "タスクの名前を入れてください．"
    elif len(title) > max_length:
        error = f"タスクの名前は{max_length}文字以内にしてください．"
    elif anchor is None:
        room = request.POST.get("room") or NO_CATEGORY
        if room != NO_CATEGORY:
            try:
                task.category = request.user.categories.get(pk=int(room))
            except (ValueError, Category.DoesNotExist):
                raise Http404
        task.on_map = True
    elif relation not in LINK_FIELDS:
        error = "つなぎ方を選んでください．"
    elif relation == "child":
        task.parent = anchor
    else:
        task.parent = anchor.parent
        task.prerequisite = anchor
        if anchor.parent_id is None:
            # いちばん上のタスクのあと → 自分もいちばん上のタスク。同じ部屋に置く
            task.category = anchor.category
            task.on_map = True
    if error is None:
        try:
            task.full_clean()  # 3段まで など、ほかの画面と同じルールで確かめる
        except ValidationError as validation_error:
            error = validation_error.messages[0]
    if error:
        messages.error(request, error)
        if anchor is not None:
            return _room_redirect(anchor)
        return redirect("task_map_room", key=request.POST.get("room") or NO_CATEGORY)
    task.save()
    if anchor is None:
        messages.success(request, f"「{title}」を置きました．")
    elif relation == "child":
        messages.success(request, f"「{title}」を「{anchor.title}」のサブタスクとして足しました．")
    else:
        messages.success(request, f"「{title}」を，「{anchor.title}」が終わってからやるタスクとして足しました．")
    return _room_redirect(task, focus=True)


@login_required
@require_POST
def task_unlink(request, pk):
    """マップの線や詳細画面の ✕ で、つながり（親子・前後・🔗 関連）を外す。タスクそのものは消さない。"""
    task = get_object_or_404(request.user.tasks.select_related("parent", "prerequisite", "category"), pk=pk)
    relation = request.POST.get("relation")
    if relation == "related":
        # 🔗 は相手が何件もあるので、どの相手かを target で受け取る
        target = request.POST.get("target", "")
        other = task.related.filter(pk=target).first() if target.isdigit() else None
        if other is None:
            messages.error(request, "外すつながりがありません．")
        else:
            task.related.remove(other)
            if _is_htmx(request):
                # 詳細画面の 🔗 の欄だけを書きかえる（Issue #57）
                task = request.user.tasks.prefetch_related("related").get(pk=task.pk)
                return HttpResponse(_render_part(request, "tasks/_related.html", {"task": task}))
            messages.success(request, f"「{task.title}」と「{other.title}」の 🔗 を外しました．")
        return _redirect_next(request, "task_map")
    field = LINK_FIELDS.get(relation)
    other = getattr(task, field) if field else None
    if other is None:
        messages.error(request, "外すつながりがありません．")
    else:
        setattr(task, field, None)
        fields = [field, "updated_at"]
        if relation == "child":
            # 親から出たタスクは、いちばん上のタスクになる。分類はそのままなので、同じ部屋に置いたままにする
            task.on_map = True
            fields.append("on_map")
        task.save(update_fields=fields)
        if relation == "child":
            messages.success(request, f"「{task.title}」を「{other.title}」のサブタスクから外しました（この部屋に残ります）．")
        else:
            messages.success(request, f"「{task.title}」と「{other.title}」の前後のつながりを外しました．")
    return _redirect_next(request, "task_map")


@login_required
@require_POST
def task_relink(request, pk):
    """マップの線を押して、つなぎ方を切り替える（📁 サブタスクにする ⇔ 🔒 終わってからやる）。

    relation：今のつなぎ方。
      - "child" → 🔒：親（B）が終わってからやる形にする。B と同じ段（B の親の中）に移す。
        B がいちばん上のタスクなら、自分もいちばん上のタスクになって、同じ部屋に残る
      - "after" → 📁：先にやること（B）のサブタスクにする
    """
    task = get_object_or_404(request.user.tasks.select_related("parent__parent", "prerequisite"), pk=pk)
    relation = request.POST.get("relation")
    error = None
    if relation == "child" and task.parent is not None:
        other = task.parent
        task.parent = other.parent
        task.prerequisite = other
        if task.parent is None:
            task.on_map = True
        done_message = f"「{task.title}」を，「{other.title}」が終わってからやる，に切り替えました．"
    elif relation == "after" and task.prerequisite is not None:
        other = task.prerequisite
        task.prerequisite = None
        task.parent = other
        done_message = f"「{task.title}」を「{other.title}」のサブタスクに切り替えました．"
    else:
        error = "切り替えるつながりがありません．"
    if error is None:
        try:
            task.full_clean()  # 3段まで・輪にならない など、ほかの画面と同じルールで確かめる
        except ValidationError as validation_error:
            error = validation_error.messages[0]
    if error:
        messages.error(request, error)
    else:
        task.save(update_fields=["parent", "prerequisite", "on_map", "updated_at"])
        messages.success(request, done_message)
    return _redirect_next(request, "task_map")


@login_required
@require_POST
def task_map_arrange(request, key):
    """部屋の点の置き場所を消して、自動で並べ直す（この部屋に置いたタスクだけ）。"""
    tops = request.user.tasks.filter(parent=None, on_map=True)
    if key == NO_CATEGORY:
        tops = tops.filter(category=None)
    else:
        try:
            tops = tops.filter(category=request.user.categories.get(pk=int(key)))
        except (ValueError, Category.DoesNotExist):
            raise Http404
    for top in tops:
        _clear_positions(request.user, top)
    messages.success(request, "点を自動で並べ直しました．")
    return redirect("task_map_room", key=key)


# ---- 進み具合（F-06）----

@login_required
def progress(request):
    """進み具合の画面。全体・分類ごと・サブタスクがあるタスクごとに「何件中何件終わったか」を見せる。

    数えるのは「サブタスクのないタスク」だけ。
    「卒業研究」のようなまとめ役のタスクは、サブタスクを数えれば足りるので、二重に数えない。
    値は保存せず、開くたびに計算する（設計で決めたこと）。
    """
    now = timezone.now()
    week_ago = now - timedelta(days=7)
    # 自分のタスクを1回の問い合わせでまとめて読み、あとは Python の中で数える
    tasks = list(request.user.tasks.select_related("category", "prerequisite"))
    by_id = {t.pk: t for t in tasks}
    has_children = {t.parent_id for t in tasks if t.parent_id}
    leaves = [t for t in tasks if t.pk not in has_children]

    def summary(items):
        total = len(items)
        done = sum(1 for t in items if t.is_done)
        return {
            "total": total,
            "done": done,
            "percent": round(done * 100 / total) if total else 0,
            # まだ終わっていないものを、ホームと同じ順（重要度 → 締切）で
            "open": [t for t in items if not t.is_done],
            "overdue": sum(1 for t in items if not t.is_done and t.due_at and t.due_at < now),
        }

    overall = summary(leaves)
    overall["week_done"] = sum(1 for t in leaves if t.is_done and t.done_at and t.done_at >= week_ago)

    # 分類ごと：分類を作った順に並べ、「分類なし」は最後
    by_category = defaultdict(list)
    for t in leaves:
        by_category[t.category_id].append(t)
    categories = []
    for category in request.user.categories.all():
        if by_category.get(category.pk):
            categories.append({"name": category.name, **summary(by_category[category.pk])})
    if by_category.get(None):
        categories.append({"name": "分類なし", **summary(by_category[None])})

    # サブタスクがあるタスクごと：サブタスクがあって、まだ終わっていない、いちばん上のタスク
    def root_of(task):
        while task.parent_id is not None:
            task = by_id[task.parent_id]
        return task

    by_root = defaultdict(list)
    for t in leaves:
        if t.parent_id is not None:
            by_root[root_of(t).pk].append(t)
    main_tasks = [
        {"task": t, **summary(by_root[t.pk])}
        for t in tasks
        if t.parent_id is None and not t.is_done and by_root.get(t.pk)
    ]

    return render(
        request,
        "tasks/progress.html",
        {"overall": overall, "categories": categories, "main_tasks": main_tasks},
    )


# ---- 使い方（Issue #47）----


# 使い方の章（guide/_○○.html の data-key と同じ）。ヘルプの画面に、章ごとのリンクとして並べる
GUIDE_CHAPTERS = [
    ("start", "はじめに", "➕ 足す・✔ 完了・締切の色"),
    ("tasks", "タスク", "✏️ 詳細と編集・🏷️ 分類"),
    ("links", "つながり", "📁 サブタスク・🔒 終わってから・🔗 関連"),
    ("map", "マップ", "🗺️ 部屋・📦 棚・● から線"),
    ("more", "資料・進み具合", "📎 資料・📊 進み具合"),
]


@login_required
def guide(request):
    """使い方のページ。1枚に1つの機能を、横にスワイプして見ていく（動きは guide.js）。

    登録した直後に1回開く（accounts.views.SignUpView）。そのあとは ？ → ヘルプ から。
    中身（カード）は guide.html と guide/_○○.html に書いてある。
    """
    return render(request, "tasks/guide.html")


def _from_page(request):
    """どの画面で ？ を押したか（?from=/map/room/3/）。このサイトの中の道（/ で始まる）だけを、200字まで。

    管理画面で読むためだけに使い、移動先には使わない。「?」から後ろ（検索の条件など）は残さない。
    """
    value = request.POST.get("from") or request.GET.get("from") or ""
    if not value.startswith("/") or value.startswith("//"):
        return ""
    return urlsplit(value).path[:200]


@login_required
def help_page(request):
    """ヘルプ（上のバーの ？）。使い方（章ごと）・記号の一覧・意見を送る。"""
    return render(request, "tasks/help.html", {"chapters": GUIDE_CHAPTERS, "from_page": _from_page(request)})


@login_required
def feedback_create(request):
    """✉️ 意見を送る（Issue #48）。送った意見は、管理画面の「意見」で読む。"""
    from_page = _from_page(request)
    since = timezone.now() - timedelta(days=1)
    sent_today = request.user.feedbacks.filter(created_at__gte=since).count()
    limit_reached = sent_today >= Feedback.DAILY_LIMIT
    form = FeedbackForm(request.POST or None)
    if request.method == "POST" and not limit_reached and form.is_valid():
        feedback = form.save(commit=False)
        feedback.user = request.user
        feedback.page = from_page
        feedback.save()
        messages.success(request, "✉️ 送りました．ありがとうございます！")
        return redirect("help")
    return render(
        request,
        "tasks/feedback.html",
        {"form": form, "from_page": from_page, "limit_reached": limit_reached, "limit": Feedback.DAILY_LIMIT},
    )
