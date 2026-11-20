from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models
from django.db.models import F
from django.utils import timezone


# ユーザー登録のときに用意する分類（名前の変更や削除もできる）
DEFAULT_CATEGORY_NAMES = ["授業", "実習", "資格対策", "研究"]


class Category(models.Model):
    """タスクの分類。ユーザーごとに持ち、自分で足したり名前を変えたりできる。"""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,  # ユーザーを消したら、その人の分類も消す
        related_name="categories",
        verbose_name="持ち主",
    )
    name = models.CharField("名前", max_length=50)
    created_at = models.DateTimeField("作成日時", auto_now_add=True)

    class Meta:
        verbose_name = "分類"
        verbose_name_plural = "分類"
        ordering = ["created_at", "id"]  # 作った順に並べる
        constraints = [
            # 同じ人が同じ名前の分類を2つ作れないようにする（データベースが守るルール）
            models.UniqueConstraint(fields=["owner", "name"], name="unique_category_name_per_owner"),
        ]

    def __str__(self):
        return self.name


class Task(models.Model):
    """やること1件を表すモデル。親タスクを持たせることで入れ子（3段まで）にできる。"""

    # 入れ子にできる深さの上限（例：国家試験 → 科目 → 問題集の章）
    MAX_DEPTH = 3

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
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,  # 分類を消しても、タスクは消さずに「分類なし」にする
        null=True,
        blank=True,
        related_name="tasks",
        verbose_name="分類",
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

    def due_status(self, now=None):
        """締切の状態を返す。画面で色を変えるのに使う。

        "overdue"：締切を過ぎた（未完了のとき）／"today"：今日が締切／
        "soon"：3日以内／"later"：それより先／None：締切なし
        """
        if self.due_at is None:
            return None
        now = timezone.localtime(now or timezone.now())
        due = timezone.localtime(self.due_at)
        if not self.is_done and due < now:
            return "overdue"
        days = (due.date() - now.date()).days
        if days <= 0:
            return "today"
        if days <= 3:
            return "soon"
        return "later"

    def progress(self):
        """子タスクのうち完了したものの割合（0～100）。子がなければ None。"""
        children = list(self.children.all())
        if not children:
            return None
        done = sum(1 for child in children if child.is_done)
        return round(done * 100 / len(children))


def source_photo_path(instance, filename):
    """写真を保存する場所。持ち主ごとのフォルダに、ランダムな名前（filename は images.py で付ける）で置く。"""
    return f"source_photos/{instance.task.owner_id}/{filename}"


class Source(models.Model):
    """資料（F-04）。課題の指示に関係するもの（プリント・スライド・リンク・メモ・写真）を記録する。1つのタスクに複数付けられる。

    画面では「資料」と呼ぶ（最初は「出どころ」という名前だった）。プログラムの中の名前は Source のまま。
    """

    # 1人あたりの写真の枚数の上限（サーバーの容量を使いすぎないため）
    MAX_PHOTOS_PER_USER = 100

    class Kind(models.TextChoices):
        PAPER = "paper", "紙"
        CLASSROOM = "classroom", "Classroom"
        SLIDE = "slide", "スライド"
        VERBAL = "verbal", "口頭"
        SLACK = "slack", "Slack"
        EMAIL = "email", "メール"
        OTHER = "other", "その他"

    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,  # タスクを消したら、その資料も消す
        related_name="sources",
        verbose_name="タスク",
    )
    kind = models.CharField("種類", max_length=20, choices=Kind.choices, default=Kind.PAPER)
    url = models.URLField(
        "リンク",
        max_length=500,
        blank=True,
        # http:// と https:// だけを受け付ける（javascript: などの危ないリンクを入れさせない）
        validators=[URLValidator(schemes=["http", "https"])],
    )
    memo = models.CharField("メモ", max_length=200, blank=True)
    # 写真は誰でも開ける場所には置かず、持ち主だけが見られる画面（source_photo）を通して表示する
    photo = models.ImageField("写真", upload_to=source_photo_path, blank=True)
    created_at = models.DateTimeField("作成日時", auto_now_add=True)

    class Meta:
        verbose_name = "資料"
        verbose_name_plural = "資料"
        ordering = ["created_at", "id"]  # 足した順に並べる

    def __str__(self):
        return f"{self.get_kind_display()}：{self.memo or self.url or '写真'}"

    def clean(self):
        super().clean()
        # 中身のない記録が増えないよう、リンク・メモ・写真のどれか1つは必ず入れてもらう
        if not self.url and not self.memo.strip() and not self.photo:
            raise ValidationError("リンク・メモ・写真のどれか1つ以上を入れてください．")
