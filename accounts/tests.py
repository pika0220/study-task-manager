from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


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
        self.assertRedirects(response, reverse("home"))
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
