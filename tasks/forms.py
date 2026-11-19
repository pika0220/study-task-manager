from django import forms

from .models import Task


class TaskForm(forms.ModelForm):
    """タスクの登録に使うフォーム。

    ModelForm：モデル（Task）の項目から、入力欄を自動で作ってくれるフォーム。
    持ち主（owner）は入力させず、ビューでログイン中の本人を入れる。
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
