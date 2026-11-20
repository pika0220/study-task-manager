"""シグナル：Django の中で何かが起きたときに、自動で呼ばれる関数を登録しておく仕組み。

ここでは「ユーザーが新しく作られたとき」に、最初の4つの分類を用意する。
画面からの登録でも、管理画面や createsuperuser で作ったときでも、同じように動く。
"""

from django.conf import settings
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import DEFAULT_CATEGORY_NAMES, Category, Source


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_default_categories(sender, instance, created, **kwargs):
    # created：新しく作られたときだけ True（名前の変更などで保存したときは False）
    if created:
        Category.objects.bulk_create(
            [Category(owner=instance, name=name) for name in DEFAULT_CATEGORY_NAMES]
        )


@receiver(post_delete, sender=Source)
def delete_source_photo(sender, instance, **kwargs):
    # 資料を消したとき（タスクやユーザーを消して一緒に消えたときも）、写真のファイルも消す。
    # データベースの記録だけ消えて、ファイルがサーバーに残り続けないようにする。
    if instance.photo:
        instance.photo.delete(save=False)
