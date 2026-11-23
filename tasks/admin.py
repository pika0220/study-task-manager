from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q

from .models import Feedback, Usage

# タスク・分類・資料は、管理画面に出さない（Issue #49）。
# 中身（タイトル・メモ・写真など）は持ち主だけのもの。管理画面で見られるのは、下の「利用状況」の件数だけにする。


@admin.register(Usage)
class UsageAdmin(admin.ModelAdmin):
    """利用状況：ユーザーごとに、どの機能をどれくらい使っているかの件数だけを出す（中身は出さない）。"""

    list_display = (
        "username", "date_joined", "last_login",
        "task_count", "done_count", "subtask_count", "locked_count", "related_count",
        "on_map_count", "source_count", "photo_count", "category_count",
    )
    list_display_links = None  # 1人ずつの画面（中身を変える画面）は開かない
    ordering = ("-date_joined",)
    # 一覧の上の「全体」の表（templates/admin/tasks/usage/change_list.html）
    change_list_template = "admin/tasks/usage/change_list.html"

    def get_queryset(self, request):
        # annotate：ユーザーごとの件数を、一緒に数えて持ってくる。
        # いくつも数えると表のつなぎ方で同じ行が何回も数えられるので、distinct=True（重ならないように数える）
        def count(path, **condition):
            return Count(path, filter=Q(**condition) if condition else None, distinct=True)

        return (
            super()
            .get_queryset(request)
            .annotate(
                task_total=count("tasks"),
                done_total=count("tasks", tasks__is_done=True),
                subtask_total=count("tasks", tasks__parent__isnull=False),
                locked_total=count("tasks", tasks__prerequisite__isnull=False),
                related_total=count("tasks", tasks__related__isnull=False),
                on_map_total=count("tasks", tasks__on_map=True),
                source_total=count("tasks__sources"),
                photo_total=Count("tasks__sources", filter=~Q(tasks__sources__photo=""), distinct=True),
                category_total=count("categories"),
            )
        )

    @admin.display(description="タスク", ordering="task_total")
    def task_count(self, obj):
        return obj.task_total

    @admin.display(description="✔ 完了", ordering="done_total")
    def done_count(self, obj):
        return obj.done_total

    @admin.display(description="📁 サブタスク", ordering="subtask_total")
    def subtask_count(self, obj):
        return obj.subtask_total

    @admin.display(description="🔒 あり", ordering="locked_total")
    def locked_count(self, obj):
        return obj.locked_total

    @admin.display(description="🔗 あり", ordering="related_total")
    def related_count(self, obj):
        return obj.related_total

    @admin.display(description="🗺️ 置いた", ordering="on_map_total")
    def on_map_count(self, obj):
        return obj.on_map_total

    @admin.display(description="📎 資料", ordering="source_total")
    def source_count(self, obj):
        return obj.source_total

    @admin.display(description="🖼️ 写真", ordering="photo_total")
    def photo_count(self, obj):
        return obj.photo_total

    @admin.display(description="🏷️ 分類", ordering="category_total")
    def category_count(self, obj):
        return obj.category_total

    def changelist_view(self, request, extra_context=None):
        # 全体：何人が登録していて、それぞれの機能を「1回でも使った人」が何人いるか
        users = Usage.objects.all()
        def people(**condition):
            return users.filter(**condition).distinct().count()

        summary = [
            ("登録した人", users.count()),
            ("タスクを作った", people(tasks__isnull=False)),
            ("✔ 完了にした", people(tasks__is_done=True)),
            ("📁 サブタスク", people(tasks__parent__isnull=False)),
            ("🔒 終わってから", people(tasks__prerequisite__isnull=False)),
            ("🔗 関連", people(tasks__related__isnull=False)),
            ("🗺️ マップに置いた", people(tasks__on_map=True)),
            ("📎 資料", people(tasks__sources__isnull=False)),
            ("✉️ 意見を送った", people(feedbacks__isnull=False)),
        ]
        return super().changelist_view(request, {**(extra_context or {}), "usage_summary": summary})

    # 見るだけ。1人ずつの画面は開かない（件数の一覧だけ）。足す・変える・消すもできない
    def change_view(self, request, object_id, form_url="", extra_context=None):
        raise PermissionDenied

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    """ヘルプの「✉️ 意見を送る」（Issue #48）で届いた意見。読んで、対応したら「対応済み」にする。"""

    list_display = ("created_at", "kind", "short_body", "user", "page", "is_resolved")
    list_filter = ("is_resolved", "kind")
    # 一覧の画面のまま「対応済み」を付け外しできる（下の「保存」を押す）
    list_editable = ("is_resolved",)
    search_fields = ("body",)
    # 届いた内容は書きかえない（読むだけ）。変えられるのは「対応済み」だけ
    readonly_fields = ("user", "kind", "body", "page", "created_at")
    fields = ("created_at", "user", "kind", "page", "body", "is_resolved")

    @admin.display(description="内容")
    def short_body(self, obj):
        return obj.body if len(obj.body) <= 40 else obj.body[:40] + "…"

    def has_add_permission(self, request):
        # 意見はアプリの画面から届くもの。管理画面からは作らない
        return False
