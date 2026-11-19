from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F
from django.utils import timezone


class Task(models.Model):
    """やること1件を表すモデル。親タスクを持たせることで入れ子（3段まで）にできる。"""

    # 入れ子にできる深さの上限（例：国家試験 → 科目 → 問題集の章）
    MAX_DEPTH = 3

    class Category(models.TextChoices):
        # 左がデータベースに保存する値、右が画面に表示する名前
        CLASS = "class", "授業"
        PRACTICUM = "practicum", "実習"
        EXAM = "exam", "資格対策"
        RESEARCH = "research", "研究"

    class Importance(models.IntegerChoices):
        LOW = 1, "低"
        MEDIUM = 2, "中"
        HIGH = 3, "高"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,  # ユーザーを消したら、その人のタスクも消す
        related_name="tasks",
        verbose_name="持ち主",
    )
    title = models.CharField("タイトル", max_length=200)
    memo = models.TextField("メモ", blank=True)
    due_at = models.DateTimeField("締切", null=True, blank=True)
    importance = models.PositiveSmallIntegerField(
        "重要度", choices=Importance.choices, default=Importance.MEDIUM
    )
    category = models.CharField(
        "分類", max_length=20, choices=Category.choices, default=Category.CLASS
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,  # 親を消したら子も消す
        null=True,
        blank=True,
        related_name="children",
        verbose_name="親タスク",
    )
    is_done = models.BooleanField("完了", default=False)
    done_at = models.DateTimeField("完了した日時", null=True, blank=True)
    created_at = models.DateTimeField("作成日時", auto_now_add=True)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    class Meta:
        verbose_name = "タスク"
        verbose_name_plural = "タスク"
        # 未完了を先に、重要度の高い順、締切の近い順（締切なしは最後）
        ordering = ["is_done", "-importance", F("due_at").asc(nulls_last=True)]

    def __str__(self):
        return self.title

    def depth(self):
        """一番上のタスクを1段目として、自分が何段目かを返す。"""
        level = 1
        node = self.parent
        while node is not None:
            level += 1
            node = node.parent
        return level

    def height(self):
        """自分の下に何段あるか（自分を含む）。子がなければ1。"""
        if self.pk is None:
            return 1
        return 1 + max((child.height() for child in self.children.all()), default=0)

    def clean(self):
        """保存する前のチェック。管理画面や入力フォームから自動で呼ばれる。"""
        super().clean()
        if self.parent is None:
            return

        if self.owner_id and self.parent.owner_id != self.owner_id:
            raise ValidationError({"parent": "他の人のタスクを親にはできません。"})

        # 自分自身や自分の子孫を親にすると、親子が輪になってしまう
        node = self.parent
        while node is not None:
            if self.pk is not None and node.pk == self.pk:
                raise ValidationError({"parent": "自分自身や自分の子タスクを親にはできません。"})
            node = node.parent

        if self.parent.depth() + self.height() > self.MAX_DEPTH:
            raise ValidationError(
                {"parent": f"タスクの入れ子は{self.MAX_DEPTH}段までです。"}
            )

    def save(self, *args, **kwargs):
        # 完了にしたら日時を記録し、未完了に戻したら消す
        if self.is_done and self.done_at is None:
            self.done_at = timezone.now()
        elif not self.is_done:
            self.done_at = None
        super().save(*args, **kwargs)

    def progress(self):
        """子タスクのうち完了したものの割合（0～100）。子がなければ None。"""
        children = list(self.children.all())
        if not children:
            return None
        done = sum(1 for child in children if child.is_done)
        return round(done * 100 / len(children))
