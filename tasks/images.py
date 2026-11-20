"""アップロードされた写真を、安全な形に描き直す処理。

送られてきたファイルは信用しない、が基本。次の順に確かめて、新しいJPEGとして作り直す。
1. 大きさ（バイト数）の上限
2. Pillowで開いて、本当に画像か・受け付ける形式（JPEG・PNG・WebP）か
3. 縦横の画素数の上限（小さなファイルが開くと巨大になる「解凍爆弾」を防ぐ）
4. 向きを直してから描き直す → 撮影場所・機種などの情報（EXIF）や、画像に仕込まれたデータは消える
5. 長い辺を MAX_SIDE まで縮め、ランダムな名前を付ける
"""

import io
import uuid
import warnings

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BYTES = 10 * 1024 * 1024  # 10MB
MAX_PIXELS = 50_000_000  # 縦×横の画素数の上限（スマホの写真は大きくても5000万画素ほど）
MAX_SIDE = 2000  # 保存するときの長い辺の画素数
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


def sanitize_photo(upload):
    """アップロードされたファイルを確かめ、描き直したJPEG（ContentFile）を返す。だめなら ValidationError。"""
    if upload.size > MAX_BYTES:
        raise ValidationError("写真は10MBまでにしてください．")

    try:
        # 解凍爆弾の警告は、エラーとして扱って止める
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            upload.seek(0)
            with Image.open(upload) as probe:
                image_format = probe.format
                width, height = probe.size
                probe.verify()  # ファイルが壊れていないか（中身が画像として正しいか）
            if image_format not in ALLOWED_FORMATS:
                raise ValidationError("JPEG・PNG・WebPの写真だけをのせられます．")
            if width * height > MAX_PIXELS:
                raise ValidationError("写真の画素数が大きすぎます．")

            # verify() のあとは開き直す決まり
            upload.seek(0)
            with Image.open(upload) as image:
                image = ImageOps.exif_transpose(image)  # スマホの写真の向きを正しくする
                image = image.convert("RGB")  # 透明部分などをなくし、JPEGにできる形にする
                image.thumbnail((MAX_SIDE, MAX_SIDE))  # 縦横の比を保ったまま縮める
                buffer = io.BytesIO()
                # exif を渡さずに保存するので、撮影場所などの情報は残らない
                image.save(buffer, format="JPEG", quality=85, optimize=True)
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, SyntaxError):
        raise ValidationError("画像として読み込めないファイルです．")

    # 送られてきたファイル名は使わず、ランダムな名前にする
    return ContentFile(buffer.getvalue(), name=f"{uuid.uuid4().hex}.jpg")
