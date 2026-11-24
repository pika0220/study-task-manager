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


# パスワードの確かめ方（Issue #59）：新しいパスワードは Argon2id で保存し、
# 前の方式（PBKDF2）で保存した人も、そのままログインできて、そのときに Argon2id へ置きかわる
@override_settings(INVITE_CODE="", SIGNUP_WITHOUT_INVITE=True)
class PasswordHasherTests(TestCase):
    password = "Benkyo-2026-pass"
    # 保存した文字の頭：方式・Argon2 の種類・版・設定（メモリ19MiB・2回・1並列）
    argon2_prefix = "argon2$argon2id$v=19$m=19456,t=2,p=1$"

    def stored(self, username):
        return get_user_model().objects.get(username=username).password

    def make_old_user(self, username):
        # 前の方式で保存した人を作る。本物は100万回だが、テストを速くするため1000回にする（仕組みは同じ）
        from django.contrib.auth.hashers import PBKDF2PasswordHasher

        hasher = PBKDF2PasswordHasher()
        user = get_user_model().objects.create(username=username)
        user.password = hasher.encode(self.password, hasher.salt(), iterations=1000)
        user.save()
        return user

    def test_signup_saves_argon2id(self):
        self.client.post(
            reverse("signup"),
            {"username": "kango", "password1": self.password, "password2": self.password},
        )
        self.assertTrue(self.stored("kango").startswith(self.argon2_prefix))

    def test_new_user_can_log_in(self):
        get_user_model().objects.create_user("yakugaku", password=self.password)
        self.assertTrue(self.stored("yakugaku").startswith(self.argon2_prefix))
        response = self.client.post(
            reverse("login"), {"username": "yakugaku", "password": self.password}
        )
        self.assertRedirects(response, reverse("home"))

    def test_old_user_logs_in_and_is_upgraded(self):
        self.make_old_user("mae")
        self.assertTrue(self.stored("mae").startswith("pbkdf2_sha256$"))
        response = self.client.post(
            reverse("login"), {"username": "mae", "password": self.password}
        )
        self.assertRedirects(response, reverse("home"))
        # ログインできたときに、新しい方式で保存し直している
        self.assertTrue(self.stored("mae").startswith(self.argon2_prefix))
        # 置きかわったあとも、同じパスワードでログインできる
        self.client.post(reverse("logout"))
        response = self.client.post(
            reverse("login"), {"username": "mae", "password": self.password}
        )
        self.assertRedirects(response, reverse("home"))

    def test_wrong_password_does_not_upgrade(self):
        self.make_old_user("mae")
        before = self.stored("mae")
        response = self.client.post(reverse("login"), {"username": "mae", "password": "machigai"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.stored("mae"), before)

    def test_argon2_is_fast(self):
        # 前の方式（このパソコンで約1秒）より、ずっと速いこと。遅いパソコンでも落ちないよう、余裕を持って0.5秒にする
        import time

        from django.contrib.auth.hashers import make_password

        start = time.perf_counter()
        make_password(self.password)
        self.assertLess(time.perf_counter() - start, 0.5)
