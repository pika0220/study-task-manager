from django import forms
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError

from .models import Task


class TaskForm(forms.ModelForm):
    """タスクの登録・編集に使うフォーム。

    ModelForm：モデル（Task）の項目から、入力欄を自動で作ってくれるフォーム。
    持ち主（owner）と親タスク（parent）は入力させず、ビューで決めて入れる。
    """

    class Meta:
        model = Task
        fields = ["title", "due_at", "importance", "category", "memo"]
        widgets = {
            "title": forms.TextInput(
                attrs={"class": "input w-full", "placeholder": "例：実習記録を書く", "autofocus": True}
            ),
            # type="datetime-local"：スマホでは日付と時刻を選ぶ画面が開く
            "due_at": forms.DateTimeInput(
                attrs={"class": "input w-full", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "importance": forms.Select(attrs={"class": "select w-full"}),
            "category": forms.Select(attrs={"class": "select w-full"}),
            "memo": forms.Textarea(attrs={"class": "textarea w-full", "rows": 3}),
        }

    def add_error(self, field, error):
        # モデルの clean()（3段までのチェックなど）は、エラーを「parent」の欄に付けて返す。
        # このフォームには parent の欄がないので、そのままだと Django がエラーで止まってしまう。
        # そこで parent のエラーは「フォーム全体のエラー」として画面の上に出す。
        if field is None and isinstance(error, ValidationError) and hasattr(error, "error_dict"):
            if "parent" in error.error_dict and "parent" not in self.fields:
                error_dict = dict(error.error_dict)
                parent_errors = error_dict.pop("parent")
                error_dict.setdefault(NON_FIELD_ERRORS, []).extend(parent_errors)
                error = ValidationError(error_dict)
        super().add_error(field, error)
