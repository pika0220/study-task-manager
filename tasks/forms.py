from collections import defaultdict

from django import forms
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.urls import reverse
from django.utils.html import format_html

from .images import sanitize_photo
from .models import Category, Feedback, Source, Task

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


def prerequisite_options(user, editing=None):
    """「先にやること」として選べるタスクを、木の形の順に (タスク, 見出し) で返す。

    - 自分の、未完了のタスク（今選んでいるものは、完了していても残す）
    - 編集中は、自分自身・自分の親や子孫・前後が輪になるもの（自分のあとにやるタスク）を出さない
    見出しは「国家試験 › 薬理学」のように親からの道すじにして、同じ名前のタスクを見分けやすくする。
    """
    tasks = list(user.tasks.all())
    by_id = {task.pk: task for task in tasks}
    children = defaultdict(list)
    dependents = defaultdict(list)  # 先にやるタスクの番号 → それを待っているタスク
    for task in tasks:
        children[task.parent_id].append(task)
        if task.prerequisite_id:
            dependents[task.prerequisite_id].append(task)

    blocked = set()
    if editing is not None and editing.pk is not None:
        # 自分と子孫
        stack = [editing.pk]
        while stack:
            pk = stack.pop()
            blocked.add(pk)
            stack.extend(child.pk for child in children[pk])
        # 親・親の親…
        node = by_id.get(editing.parent_id)
        while node is not None:
            blocked.add(node.pk)
            node = by_id.get(node.parent_id)
        # 自分のあとにやるタスク（選ぶと輪になる）
        stack = [editing.pk]
        while stack:
            pk = stack.pop()
            for task in dependents[pk]:
                if task.pk not in blocked:
                    blocked.add(task.pk)
                    stack.append(task.pk)

    current_id = editing.prerequisite_id if editing is not None else None
    result = []

    def walk(parent_id, path):
        for task in children[parent_id]:
            label = " › ".join(path + [task.title])
            if task.pk not in blocked and (not task.is_done or task.pk == current_id):
                result.append((task, label))
            walk(task.pk, path + [task.title])

    walk(None, [])
    return result


class TaskForm(forms.ModelForm):
    """タスクの登録・編集に使うフォーム。

    ModelForm：モデル（Task）の項目から、入力欄を自動で作ってくれるフォーム。
    持ち主（owner）は入力させず、ビューでログイン中の本人を入れる。
    使うときは user=ログイン中のユーザー を渡す（親タスクの選択肢を作るため）。
    """

    class Meta:
        model = Task
        fields = ["title", "parent", "prerequisite", "due_at", "importance", "category", "memo"]
        # 用語（親・前後）ではなく、使う人が自分に問いかける形にする。
        # アイコンは画面全体でそろえる：📁＝サブタスク（親子）、🔒＝終わってから（前後）
        labels = {
            "parent": "📁 どのタスクのサブタスク？",
            "prerequisite": "🔒 何が終わってから取りかかる？",
        }
        help_texts = {
            "parent": "例：卒業研究 → 📁 文献調査",
            "prerequisite": "例：文献調査 ┅▶ 構成を考える（終わるまで 🔒）",
        }
        field_classes = {"category": CategoryChoiceField}
        widgets = {
            "title": forms.TextInput(
                attrs={"class": "input w-full", "placeholder": "例：実習記録を書く", "autofocus": True}
            ),
            "parent": forms.Select(attrs={"class": "select w-full"}),
            "prerequisite": forms.Select(attrs={"class": "select w-full"}),
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

    field_order = ["title", "parent", "prerequisite", "due_at", "importance", "category", "new_category_name", "memo"]

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
        field.choices = [("", "なし（いちばん上のタスク）")] + [
            (task.pk, "　" * (depth - 1) + ("└ " if depth > 1 else "") + task.title)
            for task, depth in options
        ]

        # 親を選んだときに「分類 ＝ ○○」と見せるための、親の番号 → 分類の名前（画面の JavaScript に渡す）。
        # サブタスクの分類はいちばん上のタスクにそろっているので、親自身の分類を見ればよい
        names = {c.pk: c.name for c in category.queryset}
        self.parent_category_names = {
            str(task.pk): names.get(task.category_id, "分類なし") for task, _ in options
        }

        # 先にやること：同じように、選べるタスクだけに絞る
        pre_options = prerequisite_options(user, editing=self.instance)
        pre = self.fields["prerequisite"]
        pre.queryset = Task.objects.filter(pk__in=[task.pk for task, _ in pre_options])
        pre.choices = [("", "なし")] + [(task.pk, label) for task, label in pre_options]

    # 画面で「つながり」の枠にまとめて出す欄
    RELATION_FIELDS = ("parent", "prerequisite")

    def relation_fields(self):
        """つながりの欄（親子・前後）だけを返す。"""
        return [self[name] for name in self.RELATION_FIELDS]

    def detail_fields(self):
        """タイトルとつながり以外の欄（締切・重要度・分類・メモなど）を返す。"""
        return [field for field in self if field.name not in self.RELATION_FIELDS + ("title",)]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("parent"):
            # 親を選んだときは、分類は親（いちばん上のタスク）と同じになる（モデルの save() がそろえる）。
            # 分類の欄は画面で隠れているので、送られてきた値は使わない
            cleaned["category"] = None
            return cleaned
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
    """資料を足すフォーム。詳細画面の中に小さく置く。使うときは owner=ログイン中のユーザー を渡す。"""

    # assume_scheme="https"：「example.com」のように http を省いて書かれたら https:// を補う
    url = forms.URLField(
        label="リンク",
        required=False,
        max_length=500,
        assume_scheme="https",
        widget=forms.URLInput(attrs={"class": "input w-full", "placeholder": "https://..."}),
    )

    # 写真：受け取ったファイルはそのまま使わず、clean_photo で確かめて描き直す
    photo = forms.FileField(
        label="写真",
        required=False,
        widget=forms.FileInput(
            attrs={"class": "file-input w-full", "accept": "image/jpeg,image/png,image/webp"}
        ),
        help_text="個人情報（患者さん・利用者さんの名前など）が写っている写真はのせないでください．撮影場所などの情報は自動で消します．",
    )

    class Meta:
        model = Source
        fields = ["kind", "url", "memo", "photo"]
        labels = {"memo": "メモ"}
        widgets = {
            "kind": forms.Select(attrs={"class": "select w-full"}),
            "memo": forms.TextInput(attrs={"class": "input w-full", "placeholder": "例：3限の配布プリント"}),
        }

    def __init__(self, *args, owner=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner

    def clean_photo(self):
        upload = self.cleaned_data.get("photo")
        if not upload:
            return None
        if self.owner is not None:
            count = Source.objects.filter(task__owner=self.owner).exclude(photo="").count()
            if count >= Source.MAX_PHOTOS_PER_USER:
                raise ValidationError(f"写真は1人{Source.MAX_PHOTOS_PER_USER}枚までです．使わない写真を消してください．")
        return sanitize_photo(upload)


class FeedbackForm(forms.ModelForm):
    """ヘルプの「✉️ 意見を送る」（Issue #48）。種類を選んで、内容を書く。"""

    # 種類は3つから選ぶだけ。ModelForm が自動で足す「---------」（選ばない）は出さない
    kind = forms.ChoiceField(label="どんな意見？", choices=Feedback.Kind.choices, widget=forms.RadioSelect)

    class Meta:
        model = Feedback
        fields = ["kind", "body"]
        widgets = {
            "body": forms.Textarea(
                attrs={
                    "class": "textarea w-full",
                    "rows": 6,
                    "maxlength": 1000,
                    "placeholder": "例：マップで線を引くと，点がずれる",
                }
            ),
        }
        labels = {"body": "内容（1000字まで）"}
