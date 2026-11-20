from collections import defaultdict

from django import forms
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError

from .models import Task


def parent_options(user, editing=None):
    """親タスクとして選べるタスクを、木の形の順に (タスク, 段) で返す。

    - 自分のタスクだけ
    - 未完了のものだけ（編集中のタスクの今の親は、完了していても残す）
    - 付けたあとに3段を超えるものは出さない
    - 編集中は、自分自身と自分の子孫を出さない（親子が輪になるため）
    データベースへの問い合わせは、自分のタスクを全部読む1回だけにして、あとはPythonで組み立てる。
    """
    tasks = list(user.tasks.all())  # 並び順はモデルの Meta.ordering
    children = defaultdict(list)  # 親の番号 → 子のリスト
    for task in tasks:
        children[task.parent_id].append(task)

    # 編集中のタスクとその子孫（親にできない）と、編集中のタスクの高さ（自分を含めて下に何段あるか）
    blocked = set()
    own_height = 1
    if editing is not None and editing.pk is not None:
        def height(pk):
            return 1 + max((height(child.pk) for child in children[pk]), default=0)

        own_height = height(editing.pk)
        stack = [editing.pk]
        while stack:
            pk = stack.pop()
            blocked.add(pk)
            stack.extend(child.pk for child in children[pk])

    current_parent_id = editing.parent_id if editing is not None else None
    result = []

    def walk(parent_id, depth):
        for task in children[parent_id]:
            if task.pk in blocked:
                continue
            fits = depth + own_height <= Task.MAX_DEPTH
            if fits and (not task.is_done or task.pk == current_parent_id):
                result.append((task, depth))
            walk(task.pk, depth + 1)

    walk(None, 1)
    return result


class TaskForm(forms.ModelForm):
    """タスクの登録・編集に使うフォーム。

    ModelForm：モデル（Task）の項目から、入力欄を自動で作ってくれるフォーム。
    持ち主（owner）は入力させず、ビューでログイン中の本人を入れる。
    使うときは user=ログイン中のユーザー を渡す（親タスクの選択肢を作るため）。
    """

    class Meta:
        model = Task
        fields = ["title", "parent", "due_at", "importance", "category", "memo"]
        labels = {"parent": "親タスク（なくてもよい）"}
        widgets = {
            "title": forms.TextInput(
                attrs={"class": "input w-full", "placeholder": "例：実習記録を書く", "autofocus": True}
            ),
            "parent": forms.Select(attrs={"class": "select w-full"}),
            # type="datetime-local"：スマホでは日付と時刻を選ぶ画面が開く
            "due_at": forms.DateTimeInput(
                attrs={"class": "input w-full", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "importance": forms.Select(attrs={"class": "select w-full"}),
            "category": forms.Select(attrs={"class": "select w-full"}),
            "memo": forms.Textarea(attrs={"class": "textarea w-full", "rows": 3}),
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        options = parent_options(user, editing=self.instance)
        field = self.fields["parent"]
        # 選べるのは options に入ったタスクだけ（ほかの番号を送られても「正しくない選択」になる）
        field.queryset = Task.objects.filter(pk__in=[task.pk for task, _ in options])
        # 親の下に子を字下げして並べる。全角スペースは、ブラウザで詰められずに残る
        field.choices = [("", "なし（一番上のタスク）")] + [
            (task.pk, "　" * (depth - 1) + ("└ " if depth > 1 else "") + task.title)
            for task, depth in options
        ]

    def add_error(self, field, error):
        # モデルの clean()（3段までのチェックなど）は、エラーを「parent」の欄に付けて返す。
        # parent の欄がないフォームで使われたときも止まらないよう、そのときはフォーム全体のエラーにする。
        if field is None and isinstance(error, ValidationError) and hasattr(error, "error_dict"):
            if "parent" in error.error_dict and "parent" not in self.fields:
                error_dict = dict(error.error_dict)
                parent_errors = error_dict.pop("parent")
                error_dict.setdefault(NON_FIELD_ERRORS, []).extend(parent_errors)
                error = ValidationError(error_dict)
        super().add_error(field, error)
