from collections import defaultdict

from django import forms
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.urls import reverse
from django.utils.html import format_html

from .models import Category, Source, Task

NEW_CATEGORY = "__new__"  # 分類の選択肢「＋ 新しい分類…」の値


class CategoryChoiceField(forms.ModelChoiceField):
    """分類を選ぶ欄。ふつうの選択肢に加えて「＋ 新しい分類…」（NEW_CATEGORY）も受け付ける。"""

    def to_python(self, value):
        if value == NEW_CATEGORY:
            return NEW_CATEGORY
        return super().to_python(value)

    def validate(self, value):
        if value == NEW_CATEGORY:
            return
        super().validate(value)


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
        field_classes = {"category": CategoryChoiceField}
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

    # 「＋ 新しい分類…」を選んだときに名前を書く欄（モデルにはない、フォームだけの欄）
    new_category_name = forms.CharField(
        label="新しい分類の名前",
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={"class": "input w-full", "placeholder": "例：部活"}),
    )

    field_order = ["title", "parent", "due_at", "importance", "category", "new_category_name", "memo"]

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self._new_category_name = None

        # 分類：自分の分類だけを、作った順に。最後に「＋ 新しい分類…」
        category = self.fields["category"]
        category.queryset = user.categories.all()
        category.choices = (
            [("", "分類なし")]
            + [(c.pk, c.name) for c in category.queryset]
            + [(NEW_CATEGORY, "＋ 新しい分類…")]
        )
        category.help_text = format_html(
            '<a href="{}" class="link">分類の名前を変える・消す</a>', reverse("category_list")
        )

        options = parent_options(user, editing=self.instance)
        field = self.fields["parent"]
        # 選べるのは options に入ったタスクだけ（ほかの番号を送られても「正しくない選択」になる）
        field.queryset = Task.objects.filter(pk__in=[task.pk for task, _ in options])
        # 親の下に子を字下げして並べる。全角スペースは、ブラウザで詰められずに残る
        field.choices = [("", "なし（一番上のタスク）")] + [
            (task.pk, "　" * (depth - 1) + ("└ " if depth > 1 else "") + task.title)
            for task, depth in options
        ]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("category") == NEW_CATEGORY:
            name = (cleaned.get("new_category_name") or "").strip()
            if not name:
                self.add_error("new_category_name", "新しい分類の名前を入れてください．")
                cleaned["category"] = None
            else:
                # 同じ名前の分類がもうあれば、それを使う（書き方がずれて2つにならないように）
                existing = self.user.categories.filter(name=name).first()
                if existing:
                    cleaned["category"] = existing
                else:
                    # 実際に作るのは save() のとき（ほかの欄にエラーがあったら作らない）
                    self._new_category_name = name
                    cleaned["category"] = None
        return cleaned

    def save(self, commit=True):
        if self._new_category_name:
            self.instance.category = Category.objects.create(owner=self.user, name=self._new_category_name)
        return super().save(commit)

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


class SourceForm(forms.ModelForm):
    """指示の出どころを足すフォーム。詳細画面の中に小さく置く。"""

    # assume_scheme="https"：「example.com」のように http を省いて書かれたら https:// を補う
    url = forms.URLField(
        label="リンク",
        required=False,
        max_length=500,
        assume_scheme="https",
        widget=forms.URLInput(attrs={"class": "input w-full", "placeholder": "https://..."}),
    )

    class Meta:
        model = Source
        fields = ["kind", "url", "memo"]
        labels = {"memo": "メモ"}
        widgets = {
            "kind": forms.Select(attrs={"class": "select w-full"}),
            "memo": forms.TextInput(attrs={"class": "input w-full", "placeholder": "例：3限の配布プリント"}),
        }
