from django.contrib import admin

from .models import Category, Source, Task


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
