"""公開用の設定（Issue #45）のテスト。

settings.py は起動するときに1回だけ読まれるので、環境変数を変えた「別の Python」を立ち上げて確かめる。
"""
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet
from django.test import RequestFactory, SimpleTestCase
from django.views.defaults import server_error

BASE_DIR = Path(__file__).resolve().parent.parent
# 本番（DJANGO_DEBUG=0）に入れるもの。鍵はテストのたびに作るランダムな文字列
PRODUCTION = {
    "DJANGO_DEBUG": "0",
    "DJANGO_ALLOWED_HOSTS": "example.com",
    "DJANGO_SECRET_KEY": secrets.token_urlsafe(50),
    "DJANGO_FIELD_KEY": Fernet.generate_key().decode(),  # 暗号化の鍵（Issue #49）
}


def run_django(env, *args):
    """環境変数 env で manage.py を動かし、(終わったときの番号, 出力) を返す。"""
    full_env = {k: v for k, v in os.environ.items() if not k.startswith("DJANGO_")}
    full_env.update(env, PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=BASE_DIR, env=full_env, capture_output=True, text=True, encoding="utf-8",
    )
    return result.returncode, result.stdout + result.stderr


SHOW_SETTINGS = "from django.conf import settings as s; print(s.SECURE_SSL_REDIRECT, s.SESSION_COOKIE_SECURE, s.CSRF_COOKIE_SECURE, s.SECURE_HSTS_SECONDS)"


class ProductionSettingsTests(SimpleTestCase):
    def test_production_passes_the_deploy_check(self):
        code, output = run_django(PRODUCTION, "check", "--deploy", "--fail-level", "WARNING")
        self.assertEqual(code, 0, output)
        code, output = run_django(PRODUCTION, "shell", "-c", SHOW_SETTINGS)
        self.assertEqual(output.strip().splitlines()[-1], "True True True 3600")

    def test_development_keeps_plain_http(self):
        code, output = run_django({}, "shell", "-c", SHOW_SETTINGS)
        self.assertEqual(output.strip().splitlines()[-1], "False False False 0")

    def test_production_refuses_the_dev_key_or_no_hosts(self):
        for missing in ("DJANGO_SECRET_KEY", "DJANGO_ALLOWED_HOSTS", "DJANGO_FIELD_KEY"):
            with self.subTest(missing=missing):
                env = {k: v for k, v in PRODUCTION.items() if k != missing}
                code, output = run_django(env, "check")
                self.assertNotEqual(code, 0)
                self.assertIn(missing, output)

    def test_production_refuses_the_dev_field_key(self):
        from config.settings import DEV_FIELD_KEY

        env = {**PRODUCTION, "DJANGO_FIELD_KEY": f"{PRODUCTION['DJANGO_FIELD_KEY']},{DEV_FIELD_KEY}"}
        code, output = run_django(env, "check")
        self.assertNotEqual(code, 0)
        self.assertIn("DJANGO_FIELD_KEY", output)

    def test_admin_url_must_end_with_a_slash(self):
        code, output = run_django({"DJANGO_ADMIN_URL": "kanri"}, "check")
        self.assertNotEqual(code, 0)
        self.assertIn("DJANGO_ADMIN_URL", output)


class EnvFileTests(SimpleTestCase):
    """公開するサーバーの .env（Issue #53）。Web アプリもコンソールも、同じファイルから鍵を読む。"""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.folder, ignore_errors=True))
        self.env_file = self.folder / ".env"

    def write_env(self, values):
        lines = ["# コメントの行", ""] + [f"{k}={v}" for k, v in values.items()]
        self.env_file.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")

    def test_env_file_turns_on_production(self):
        self.write_env(PRODUCTION)
        code, output = run_django({"DJANGO_ENV_FILE": str(self.env_file)}, "check", "--deploy", "--fail-level", "WARNING")
        self.assertEqual(code, 0, output)
        code, output = run_django({"DJANGO_ENV_FILE": str(self.env_file)}, "shell", "-c", SHOW_SETTINGS)
        self.assertEqual(output.strip().splitlines()[-1], "True True True 3600")

    def test_real_environment_variables_win(self):
        self.write_env(PRODUCTION)
        code, output = run_django({"DJANGO_ENV_FILE": str(self.env_file), "DJANGO_DEBUG": "1"}, "shell", "-c", SHOW_SETTINGS)
        self.assertEqual(output.strip().splitlines()[-1], "False False False 0")

    def test_make_env_creates_a_working_file_and_never_overwrites(self):
        script = BASE_DIR / "scripts" / "make_env.py"
        args = [sys.executable, str(script), "--host", "example.com", "--output", str(self.env_file)]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("招待コード", result.stdout)
        first = self.env_file.read_text(encoding="utf-8")
        self.assertIn("DJANGO_ALLOWED_HOSTS=example.com", first)
        # 作ったファイルだけで、本番の設定の点検が通る
        code, output = run_django({"DJANGO_ENV_FILE": str(self.env_file)}, "check", "--deploy", "--fail-level", "WARNING")
        self.assertEqual(code, 0, output)
        # 2回目は上書きしない（暗号化の鍵が変わらないように）
        again = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertNotEqual(again.returncode, 0)
        self.assertEqual(self.env_file.read_text(encoding="utf-8"), first)


class ErrorPageTests(SimpleTestCase):
    # テストのときの Django は DEBUG=False なので、本番と同じ自前のエラー画面が出る
    databases = {"default"}  # 404 の画面は base.html（ログイン中かどうか）を使うので、データベースを読む

    def test_404_page(self):
        response = self.client.get("/no-such-page/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "ページが見つかりません", status_code=404)
        self.assertNotContains(response, "no-such-page", status_code=404)  # 探したアドレスは見せない

    def test_500_page(self):
        response = server_error(RequestFactory().get("/"))
        self.assertEqual(response.status_code, 500)
        self.assertIn("うまく動きませんでした", response.content.decode())
