from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse


# 開発のとき（招待コードなしで誰でも登録できる）の動きを確かめる
@override_settings(INVITE_CODE="", SIGNUP_WITHOUT_INVITE=True)
class AccountFlowTests(TestCase):
    password = "Benkyo-2026-pass"

    def test_login_page_opens(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ログイン")

    def test_signup_creates_user_and_logs_in(self):
        response = self.client.post(
            reverse("signup"),
            {"username": "kango", "password1": self.password, "password2": self.password},
        )
        self.assertRedirects(response, reverse("guide"))
        self.assertTrue(get_user_model().objects.filter(username="kango").exists())
        # 登録した直後からログインした状態になっている
        home = self.client.get(reverse("home"))
        self.assertContains(home, "kango さん")

    def test_signup_rejects_mismatched_passwords(self):
        response = self.client.post(
            reverse("signup"),
            {"username": "kango", "password1": self.password, "password2": "chigau-pass-2026"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(get_user_model().objects.filter(username="kango").exists())

    def test_login_then_logout(self):
        get_user_model().objects.create_user("yakugaku", password=self.password)
        response = self.client.post(
            reverse("login"), {"username": "yakugaku", "password": self.password}
        )
        self.assertRedirects(response, reverse("home"))
        response = self.client.post(reverse("logout"))
        self.assertRedirects(response, reverse("login"))
        # ログアウト後はホームを見られない
        home = self.client.get(reverse("home"))
        self.assertRedirects(home, reverse("login") + "?next=/")

    def test_forms_have_styled_inputs(self):
        # ログインと登録の入力欄に daisyUI の class が付いている
        for name in ("login", "signup"):
            response = self.client.get(reverse(name))
            self.assertContains(response, 'class="input w-full"')

    def test_wrong_password_shows_error(self):
        get_user_model().objects.create_user("yakugaku", password=self.password)
        response = self.client.post(
            reverse("login"), {"username": "yakugaku", "password": "machigai"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ユーザー名かパスワードが違います")


class InviteCodeTests(TestCase):
    """新規登録の招待コード（Issue #45）。"""

    password = "Benkyo-2026-pass"

    def signup(self, **extra):
        data = {"username": "kango", "password1": self.password, "password2": self.password, **extra}
        return self.client.post(reverse("signup"), data)

    def user_exists(self):
        return get_user_model().objects.filter(username="kango").exists()

    @override_settings(INVITE_CODE="sakura-2026")
    def test_code_field_is_shown_first(self):
        response = self.client.get(reverse("signup"))
        self.assertContains(response, "招待コード")
        self.assertEqual(list(response.context["form"].fields)[0], "invite_code")

    @override_settings(INVITE_CODE="sakura-2026")
    def test_wrong_or_missing_code_is_refused(self):
        response = self.signup(invite_code="sakura-2025")
        self.assertContains(response, "招待コードが違います．")
        self.assertEqual(response.status_code, 200)
        self.signup()
        self.assertFalse(self.user_exists())

    @override_settings(INVITE_CODE="sakura-2026")
    def test_right_code_creates_the_user(self):
        response = self.signup(invite_code=" sakura-2026 ")  # 前後の空白は気にしない
        self.assertRedirects(response, reverse("guide"))
        self.assertTrue(self.user_exists())

    @override_settings(INVITE_CODE="", SIGNUP_WITHOUT_INVITE=False)
    def test_signup_is_closed_in_production_without_a_code(self):
        response = self.client.get(reverse("signup"))
        self.assertContains(response, "今は新しく登録できません", status_code=403)
        self.assertNotContains(response, "登録する</button>", status_code=403)
        self.assertEqual(self.signup().status_code, 403)
        self.assertFalse(self.user_exists())

    @override_settings(INVITE_CODE="", SIGNUP_WITHOUT_INVITE=True)
    def test_development_needs_no_code(self):
        self.assertNotContains(self.client.get(reverse("signup")), "招待コード")
        self.assertRedirects(self.signup(), reverse("guide"))
