# 公開の手順（PythonAnywhere）

このアプリを PythonAnywhere の無料プランで公開する手順です（Issue #53）．
公開するアドレスは **https://makkytodo.pythonanywhere.com/** です．

- コマンドは，PythonAnywhere の **Bash コンソール**（Linux）で打ちます．自分のパソコンの PowerShell ではありません．
- 鍵・招待コードは，サーバーの `.env` にだけ置きます．GitHub やこのファイルには書きません．

## 0. アカウントを作る（済）

> **Note：** アドレスは `ユーザー名.pythonanywhere.com` になり，友達全員に見えます．本名や学籍番号ではない名前にしてください（あとから変えられません）．

1. https://www.pythonanywhere.com/ →「Pricing & signup」→ **Beginner（無料）** で作る．
2. 右上の「Account」→「System image」が **innit（Python 3.13）** になっていることを確かめる．

## 1. Bash コンソールを開く

上のメニューの「Consoles」→「Bash」を押す．黒い画面が開き，`~ $` と出たら準備完了．

## 2. コードを取ってくる

```bash
git clone https://github.com/pika0220/study-task-manager.git
```

- GitHub のコードを，`/home/makkytodo/study-task-manager` に写します．このリポジトリは公開なので，鍵の設定はいりません．

```bash
cd study-task-manager
```

- 以下のコマンドは，すべてこのフォルダの中で打ちます．

## 3. uv とライブラリを入れる

```bash
pip3.13 install --user uv
```

- 自分のパソコンと同じ `uv` を入れます（`--user`：自分のフォルダに入れる）．

```bash
UV_PYTHON_DOWNLOADS=never uv sync --locked --no-dev --python /usr/bin/python3.13 --no-cache
```

- `uv.lock` のとおりに，Django などを `.venv` に入れます．
  - `--python /usr/bin/python3.13` と `UV_PYTHON_DOWNLOADS=never`：PythonAnywhere にもともとある Python 3.13 を使う．Web アプリは，PythonAnywhere の Python でしか動かないため
  - `--no-cache`：一時ファイルを残さない（ディスクが 512MB しかないため）
- `uv: command not found` と出たら，`~/.local/bin/uv sync …` のように場所を付けて打ちます．

## 4. 本番の設定（.env）を作る

```bash
.venv/bin/python scripts/make_env.py --host makkytodo.pythonanywhere.com
```

- 鍵（秘密の鍵・暗号化の鍵）と招待コード，管理画面のアドレスを，その場でランダムに作って `.env` に書きます．
- 出てきた **招待コード** と **管理画面のアドレス** を控えます．
- `.env` がもうあるときは，上書きしません（暗号化の鍵が変わると，データを元に戻せなくなるため）．

```bash
cat .env
```

- **中身を全部，パスワード管理アプリなど別の安全な場所に控えます．** とくに `DJANGO_FIELD_KEY` をなくすと，暗号化したデータは誰にも元に戻せません．

## 5. データベースと静的ファイル

```bash
.venv/bin/python manage.py migrate
```

- データベース（`db.sqlite3`）を作ります．`.env` を読むので，Web アプリと同じ鍵が使われます．

```bash
.venv/bin/python manage.py collectstatic --noinput
```

- CSS・JavaScript・管理画面の画像を `staticfiles` に集めます（131個ほど）．

```bash
.venv/bin/python manage.py check --deploy
```

- 公開前の点検．`System check identified no issues (1 silenced).` と出れば OK です．

```bash
.venv/bin/python manage.py createsuperuser
```

- 管理画面に入る人（自分）を作ります．ユーザー名とパスワードは自分で決めて入力します（メールアドレスは空のまま Enter で大丈夫）．

## 6. Web アプリを作る

上のメニューの「Web」→「Add a new web app」．

1. 「Next」
2. **「Manual configuration」** を選ぶ（「Django」ではない．あれは新しく作るとき用）
3. **「Python 3.13」** を選ぶ → 「Next」

## 7. Web タブの設定

| 場所 | 入れるもの |
|---|---|
| Code → Source code | `/home/makkytodo/study-task-manager` |
| Code → Working directory | `/home/makkytodo/study-task-manager` |
| Virtualenv | `/home/makkytodo/study-task-manager/.venv` |
| Static files → URL | `/static/` |
| Static files → Directory | `/home/makkytodo/study-task-manager/staticfiles` |
| Security → Force HTTPS | オン |

- 写真のフォルダ（`media`）は Static files に**入れません**．写真は，持ち主かどうかを確かめる画面を通して見せるためです．

## 8. WSGI ファイル

Web タブの「Code」→「WSGI configuration file」のリンク（`/var/www/makkytodo_pythonanywhere_com_wsgi.py`）を開き，中身を**全部消して**次のように書きかえ，「Save」を押します．

```python
import os
import sys

path = "/home/makkytodo/study-task-manager"
if path not in sys.path:
    sys.path.insert(0, path)

# 鍵や招待コードは、ここではなく /home/makkytodo/study-task-manager/.env から読む（settings.py）
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()
```

## 9. 開いて確かめる

Web タブのいちばん上の緑の「Reload」を押してから，次を確かめます．

- [ ] https://makkytodo.pythonanywhere.com/ でログイン画面が出る（色やボタンの形も出ている）
- [ ] `http://` で開いても `https://` に移る
- [ ] 招待コードなしでは登録できない．招待コードを入れると登録でき，使い方が開く
- [ ] タスクを足す・マップ・写真をのせる，ができる
- [ ] 管理画面のアドレス（`/kanri-…/`）で管理画面が開く．`/admin/` は「ページが見つかりません」になる

## うまくいかないとき

まず Web タブの「Log files」→ **Error log** を見ます（新しいものは下）．

| 出ていること | 直し方 |
|---|---|
| `DJANGO_… に…を入れてください` | `.env` を確かめる（`cat .env`）．4 をやり直す必要があるのは `.env` がないときだけ |
| `DisallowedHost` | `.env` の `DJANGO_ALLOWED_HOSTS` が `makkytodo.pythonanywhere.com` か |
| 「リダイレクトが多すぎます」 | `.env` に `DJANGO_BEHIND_PROXY=1` があるか |
| 色やボタンの形が出ない | 7 の Static files と，5 の collectstatic |
| `ModuleNotFoundError: django` | 7 の Virtualenv の場所と，3 の uv sync |
| `暗号化したデータを元に戻せません` | `.env` の `DJANGO_FIELD_KEY` が，データを作ったときと同じか（控えと比べる） |

直したら，そのたびに「Reload」を押します．

## 更新のしかた（main にマージしたあと）

```bash
cd ~/study-task-manager
```

```bash
git pull
```

```bash
UV_PYTHON_DOWNLOADS=never uv sync --locked --no-dev --python /usr/bin/python3.13 --no-cache
```

```bash
.venv/bin/python manage.py migrate
```

```bash
.venv/bin/python manage.py collectstatic --noinput
```

最後に Web タブで「Reload」．

## 1か月ごとにすること

- 無料の Web アプリは1か月で止まります．Web タブの **「Run until 1 month from today」** を押すと，また1か月動きます（メールでお知らせが来ます）．止まってもデータは消えません．
- 右上の「Account」でディスクの使用量を見ます（512MB まで）．

## バックアップ

```bash
cd ~/study-task-manager && zip -r ~/backup-$(date +%Y%m%d).zip db.sqlite3 media
```

- データベースと写真を1つの zip にまとめます．「Files」タブから自分のパソコンに保存し，サーバーの zip は消します（ディスクを使うため）．
- 中身は暗号化されているので，**`.env` の `DJANGO_FIELD_KEY` がないと読めません**．鍵の控えは，バックアップとは別の場所に置きます．

## 招待コードを変える・新規登録を止める

`.env` の `DJANGO_INVITE_CODE=` を書きかえて「Reload」．空にすると新規登録が止まります（登録済みの人はそのまま使えます）．
`.env` は「Files」タブから開いて書きかえられます．
