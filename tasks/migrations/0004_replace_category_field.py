"""古い分類の欄（文字の値）を消し、新しい分類の欄（category_ref）の名前を category に変える。

0002 で新しい欄を足す → 0003 でデータを移す → 0004 で古い欄を消す、の3段階に分けると、
途中で失敗してもデータがなくならない。
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("tasks", "0003_move_category_data"),
    ]

    operations = [
        migrations.RemoveField(model_name="task", name="category"),
        migrations.RenameField(model_name="task", old_name="category_ref", new_name="category"),
        migrations.AlterField(
            model_name="task",
            name="category",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="tasks",
                to="tasks.category",
                verbose_name="分類",
            ),
        ),
    ]
