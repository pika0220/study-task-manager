from django.db import migrations


def sync_child_categories(apps, schema_editor):
    """今あるデータで、子タスク・孫タスクの分類を、いちばん上のタスクの分類にそろえる（Issue #34）。

    これまでは子タスクにも別の分類を付けられたので、親は「研究」、子は「分類なし」のような
    ちぐはぐなデータが残っていることがある。
    """
    Task = apps.get_model("tasks", "Task")
    tasks = {t.pk: t for t in Task.objects.all().only("pk", "parent_id", "category_id")}
    changed = []
    for task in tasks.values():
        if task.parent_id is None:
            continue
        root = task
        while root.parent_id is not None:
            root = tasks[root.parent_id]
        if task.category_id != root.category_id:
            task.category_id = root.category_id
            changed.append(task)
    Task.objects.bulk_update(changed, ["category"])


class Migration(migrations.Migration):
    dependencies = [
        ("tasks", "0009_reset_map_position"),
    ]

    operations = [
        # 戻すとき（migrate で前に戻る）は何もしない（前のちぐはぐな状態には戻せないため）
        migrations.RunPython(sync_child_categories, migrations.RunPython.noop),
    ]
