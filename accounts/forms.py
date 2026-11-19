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
    pass
