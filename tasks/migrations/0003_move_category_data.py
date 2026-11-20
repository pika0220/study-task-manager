"""データの移しかえ：今までの分類（文字の値）を、新しい分類（Category）につなぎかえる。

マイグレーションには「データベースの形を変える」ものと「中のデータを動かす」ものがある。
これは後者で、RunPython の中に Python で手順を書く。
"""

from django.db import migrations

# 今までデータベースに保存していた値 → 新しく作る分類の名前
OLD_VALUE_TO_NAME = {
    "class": "授業",
    "practicum": "実習",
    "exam": "資格対策",
    "research": "研究",
}


def forwards(apps, schema_editor):
    # apps.get_model：この時点のモデルの形を使う（今の models.py を直接 import しない決まり）
    User = apps.get_model("auth", "User")
    Category = apps.get_model("tasks", "Category")
    Task = apps.get_model("tasks", "Task")

    for user in User.objects.all():
        # 1. 全員に4つの分類を作る（作った順に並ぶので、授業 → 実習 → 資格対策 → 研究 の順）
        by_name = {}
        for name in OLD_VALUE_TO_NAME.values():
            by_name[name], _ = Category.objects.get_or_create(owner=user, name=name)
        # 2. その人のタスクを、文字の値に合う分類につなぐ
        for old_value, name in OLD_VALUE_TO_NAME.items():
            Task.objects.filter(owner=user, category=old_value).update(category_ref=by_name[name])


def backwards(apps, schema_editor):
    # 元に戻すとき：分類の名前から文字の値に戻す（自分で作った分類は「授業」に戻す）
    Task = apps.get_model("tasks", "Task")
    name_to_old_value = {name: value for value, name in OLD_VALUE_TO_NAME.items()}
    for task in Task.objects.select_related("category_ref"):
        name = task.category_ref.name if task.category_ref else None
        task.category = name_to_old_value.get(name, "class")
        task.save(update_fields=["category"])


class Migration(migrations.Migration):
    dependencies = [
        ("tasks", "0002_category_model"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
