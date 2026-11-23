from django.contrib import admin

from .models import Category, Feedback, Source, Task


class SourceInline(admin.TabularInline):
    # タスクの編集画面の中で、資料も一緒に見たり足したりできるようにする
    model = Source
    extra = 0


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    # 一覧に表示する項目
    list_display = ("title", "owner", "category", "importance", "due_at", "is_done", "parent")
    # 右側の絞り込み
    list_filter = ("category", "importance", "is_done")
    # 検索窓で探す項目
    search_fields = ("title", "memo")
    # 自動で入る項目は編集できないようにする
    readonly_fields = ("done_at", "created_at", "updated_at")
    inlines = [SourceInline]


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "created_at")
    list_filter = ("owner",)
    search_fields = ("name",)


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
