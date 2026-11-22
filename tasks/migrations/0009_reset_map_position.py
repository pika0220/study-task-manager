from django.db import migrations


def reset_positions(apps, schema_editor):
    """マップを「メインタスクごと・真ん中から枝で広がる形」に変えたので、
    前の形（縦に並べる形）で保存した置き場所を消して、新しい形で並べ直してもらう。"""
    Task = apps.get_model("tasks", "Task")
    Task.objects.update(map_x=None, map_y=None)


class Migration(migrations.Migration):
    dependencies = [
        ("tasks", "0008_task_map_position"),
    ]

    operations = [
        # 戻すとき（migrate で前に戻る）は何もしない
        migrations.RunPython(reset_positions, migrations.RunPython.noop),
    ]
