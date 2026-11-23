import secrets

from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm


class StyledFormMixin:
    """入力欄に daisyUI の class（input）を付ける。

    Django が作る入力欄は class が空なので、ここで付け足す。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "input w-full")


class LoginForm(StyledFormMixin, AuthenticationForm):
    pass


class SignUpForm(StyledFormMixin, UserCreationForm):
    """ユーザー登録のフォーム。招待コード（settings.INVITE_CODE）があるときは、コードの欄を足す（Issue #45）。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if settings.INVITE_CODE:
            self.fields["invite_code"] = forms.CharField(
                label="招待コード",
                help_text="招待した人から聞いたコード",
                widget=forms.TextInput(attrs={"class": "input w-full", "autocomplete": "off"}),
            )
            # 招待コードを、いちばん上に出す
            self.order_fields(["invite_code"])

    def clean_invite_code(self):
        code = self.cleaned_data["invite_code"].strip()
        # compare_digest：どこまで合っているかが、かかった時間から分からないようにくらべる
        if not secrets.compare_digest(code.encode(), settings.INVITE_CODE.encode()):
            raise forms.ValidationError("招待コードが違います．")
        return code
