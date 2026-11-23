"""タスクの中身を、暗号化して保存する部品（Issue #49）。

ねらい：データベースのファイルや写真のフォルダがそのまま漏れても、タイトル・メモ・分類名・資料・写真が読めないようにする。
管理画面には中身を出さない（admin.py）ので、管理者がふだん目にするのは件数だけになる。

できないこと（正直に書いておく）：
  アプリは画面に出すために中身を元に戻す。サーバーと鍵の両方を持っている人は、プログラムを書けば読める。
  「管理者でも絶対に読めない」にするには、スマホの中だけで暗号化する方法（エンドツーエンド暗号化）が必要になる。

しくみ：
  - Fernet（cryptography ライブラリ）：鍵1つで暗号化と復号をする。改ざんされていたら復号できない
  - 鍵は settings.FIELD_ENCRYPTION_KEYS（環境変数 DJANGO_FIELD_KEY）。いちばん前の鍵で暗号化し、
    どの鍵でも復号できる（MultiFernet）。鍵を取りかえるときは「新しい鍵,古い鍵」と並べる
  - 同じ文字でも、暗号化するたびに違う文字列になる。だから、データベースの中で
    「タイトルが○○のタスク」を探したり、並べ替えたりはできない（探すときは Python の中でくらべる）
"""

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.utils.deconstruct import deconstructible


def _fernet():
    """設定の鍵から、暗号化の道具を作る（テストで鍵を変えても効くように、毎回作る。作るのは軽い）。"""
    return MultiFernet([Fernet(key) for key in settings.FIELD_ENCRYPTION_KEYS])


def encrypt_bytes(data):
    return _fernet().encrypt(data)


def decrypt_bytes(token):
    try:
        return _fernet().decrypt(token)
    except InvalidToken:
        # 鍵が違う・中身が壊れている。黙って変な文字を出さず、原因が分かるように止める
        raise ImproperlyConfigured("暗号化したデータを元に戻せません．DJANGO_FIELD_KEY（鍵）が、保存したときと同じか確かめてください．")


def encrypt_text(value):
    """文字を暗号化する。空（None・""）はそのまま（「空かどうか」だけは分かるが、中身は分からない）。"""
    if value is None or value == "":
        return value
    return encrypt_bytes(value.encode("utf-8")).decode("ascii")


def decrypt_text(value):
    if value is None or value == "":
        return value
    return decrypt_bytes(value.encode("ascii")).decode("utf-8")


def is_encrypted_text(value):
    """もう暗号化してある文字か（今の鍵で元に戻せるか）。今あるデータを暗号化し直すときに、2回かけないために使う。"""
    try:
        decrypt_text(value)
    except (ImproperlyConfigured, UnicodeError):
        return False
    return True


class EncryptedFieldMixin:
    """保存するときに暗号化し、読むときに元に戻す欄にする。

    - データベースの列は、長さの決まりのない文字（TextField）。暗号化すると文字数が元の2倍近くに増えるため
    - 入力の文字数の上限（max_length）は、元の文字に対してチェックする（フォームやモデルのチェックはそのまま）
    - 「この文字と同じもの」などの探し方（lookup）は、暗号化した欄ではできないので、使ったらすぐエラーにする。
      黙って0件になると、気づきにくいバグになるため。使えるのは「空か（isnull）」だけ
    """

    def get_internal_type(self):
        return "TextField"

    def from_db_value(self, value, expression, connection):
        return decrypt_text(value)

    def get_db_prep_value(self, value, connection, prepared=False):
        value = super().get_db_prep_value(value, connection, prepared)
        return encrypt_text(value)

    def get_lookup(self, lookup_name):
        if lookup_name == "isnull":
            return super().get_lookup(lookup_name)
        return None


class EncryptedCharField(EncryptedFieldMixin, models.CharField):
    """暗号化して保存する CharField（1行の文字）。"""


class EncryptedTextField(EncryptedFieldMixin, models.TextField):
    """暗号化して保存する TextField（何行もの文字）。"""


class EncryptedURLField(EncryptedFieldMixin, models.URLField):
    """暗号化して保存する URLField（リンク）。http・https だけ、などのチェックはそのまま。"""


@deconstructible
class EncryptedFileSystemStorage(FileSystemStorage):
    """ファイル（資料の写真）を、暗号化してフォルダに置く。開くときだけ元に戻す。

    写真のフォルダは公開していないので、写真は source_photo の画面（持ち主だけ）を通して見る。
    """

    def _save(self, name, content):
        content.seek(0)
        return super()._save(name, ContentFile(encrypt_bytes(content.read())))

    def _open(self, name, mode="rb"):
        with super()._open(name, "rb") as file:
            token = file.read()
        return ContentFile(decrypt_bytes(token), name=name)


def photo_storage():
    """写真の置き場所。マイグレーションには、この関数の名前だけが書かれる（設定の中身は書かれない）。"""
    return EncryptedFileSystemStorage()
