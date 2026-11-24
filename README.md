# study-task-manager

[![test](https://github.com/pika0220/study-task-manager/actions/workflows/test.yml/badge.svg)](https://github.com/pika0220/study-task-manager/actions/workflows/test.yml)

実習・国家試験の対策・授業の課題を並行して抱える大学生のための、タスク管理Webアプリです。

紙・Classroom・スライドなどに散らばった課題の指示を1か所に集め、長期の目標と日々のタスクを見通せるようにすることを目指しています。

## 背景

大学生16人へのアンケートで、一番多い困りごとは「課題の情報がいろいろな場所に散らばっている」ことでした。また、16人中15人が「これが終わらないと次に進めない」という作業の順番で困った経験がありました。

## 最初の版（MVP）で作る機能

- アカウント登録とログイン
- タスクの登録（締切、重要度、分類：授業・実習・資格対策・研究）
- タスクの中に小さなタスクを作る（3段まで）
- 指示の出どころ（紙・Classroom・スライドなど）とリンクの記録
- 今日・今週のタスクを重要度と締切の順に表示
- 分類ごとの進み具合
- メールでのリマインド
- 前提タスクと「今すぐ取りかかれる」タスクの表示
- 試験日などのゴールから逆算した中間目標

## 使っている技術

- Python 3.13
- Django 5.2
- uv（パッケージと仮想環境の管理）

## 自分のパソコンで動かす

```bash
uv sync
uv run python manage.py migrate
uv run python manage.py runserver
```

ブラウザで http://127.0.0.1:8000/ を開きます。


## 見た目（CSS）を作り直す

画面の CSS は、Tailwind CSS と daisyUI で前もって1つのファイル（`static/css/app.css`）に作っています（Issue #55）。
テンプレートや JavaScript で新しい class を使ったら、作り直してコミットします（CI が作り忘れを確かめます）。

道具は GitHub に置いていないので、最初に1回だけ `tools` に入れます（Windows・PowerShell）。

```powershell
New-Item -ItemType Directory -Force tools
Invoke-WebRequest -Uri https://github.com/tailwindlabs/tailwindcss/releases/download/v4.3.4/tailwindcss-windows-x64.exe -OutFile tools\tailwindcss.exe
Invoke-WebRequest -Uri https://github.com/saadeghi/daisyui/releases/download/v5.7.50/daisyui.mjs -OutFile tools\daisyui.mjs
```

作り直すときは、次のコマンドを打ちます。

```powershell
tools\tailwindcss.exe -i assets\css\app.css -o static\css\app.css --minify
```

## 公開する

PythonAnywhere（無料プラン）で公開しています。手順は [docs/deploy.md](docs/deploy.md) にあります。
