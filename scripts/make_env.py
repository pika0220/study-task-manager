"""公開するサーバー用の .env（鍵・招待コードなど）を作る（Issue #53）。

使い方（PythonAnywhere の Bash コンソールで、プロジェクトのフォルダから）：
    .venv/bin/python scripts/make_env.py --host makkytodo.pythonanywhere.com

- 鍵はその場でランダムに作る（どこにも送らない・GitHub にも置かない）
- .env がもうあるときは、上書きしない。暗号化の鍵（DJANGO_FIELD_KEY）が変わると、
  それまでに暗号化したデータを、誰も元に戻せなくなるため
- 作ったあとは、中身（とくに DJANGO_FIELD_KEY）を、パスワード管理アプリなど別の安全な場所にも控える
"""

import argparse
import secrets
import sys
from pathlib import Path

from cryptography.fernet import Fernet

PROJECT_DIR = Path(__file__).resolve().parent.parent
# 招待コードに使う文字：スマホで打ちやすく、見まちがえやすい文字（0 と o、1 と l など）を除く
INVITE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def build_env(host):
    invite_code = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(8))
    admin_url = f"kanri-{secrets.token_hex(4)}/"
    lines = [
        "# 本番の設定（Issue #53）。GitHub には置かない（.gitignore で除外している）",
        "# ⚠ DJANGO_FIELD_KEY をなくすと、暗号化したデータは誰にも元に戻せない。別の安全な場所にも控えておく",
        "DJANGO_DEBUG=0",
        f"DJANGO_ALLOWED_HOSTS={host}",
        "# PythonAnywhere は前にいるサーバーで https を受けて、アプリに渡す（https かどうかは X-Forwarded-Proto で見る）",
        "DJANGO_BEHIND_PROXY=1",
        f"DJANGO_SECRET_KEY={secrets.token_urlsafe(50)}",
        f"DJANGO_FIELD_KEY={Fernet.generate_key().decode()}",
        f"DJANGO_INVITE_CODE={invite_code}",
        f"DJANGO_ADMIN_URL={admin_url}",
    ]
    return "\n".join(lines) + "\n", invite_code, admin_url


def main(argv=None):
    parser = argparse.ArgumentParser(description="公開するサーバー用の .env を作る")
    parser.add_argument("--host", required=True, help="公開するアドレス（例：makkytodo.pythonanywhere.com）")
    parser.add_argument("--output", default=str(PROJECT_DIR / ".env"), help="書き出す場所（ふつうは指定しない）")
    args = parser.parse_args(argv)

    output = Path(args.output)
    if output.exists():
        print(f"{output} はもうあります．上書きしません（暗号化の鍵が変わると，データを元に戻せなくなるため）．", file=sys.stderr)
        return 1

    content, invite_code, admin_url = build_env(args.host)
    output.write_text(content, encoding="utf-8")
    try:
        output.chmod(0o600)  # 自分だけが読み書きできるようにする（Windows では効かないが、問題はない）
    except OSError:
        pass

    print(f"作りました：{output}")
    print(f"  招待コード（友達に教える）：{invite_code}")
    print(f"  管理画面のアドレス：https://{args.host}/{admin_url}")
    print("⚠ cat .env で中身を見て，パスワード管理アプリなど別の安全な場所にも控えてください．")
    return 0


if __name__ == "__main__":
    sys.exit(main())
