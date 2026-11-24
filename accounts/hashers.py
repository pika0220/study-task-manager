"""パスワードの確かめ方（Issue #59）。

Django は、パスワードをそのまま保存せず、元に戻せない計算（ハッシュ）をした結果だけを保存する。
ログインのときは、入力されたパスワードに同じ計算をして、保存した結果と同じかを比べる。

前は Django の標準（PBKDF2 を100万回まわす）を使っていたが、公開中のサーバー（PythonAnywhere の無料プラン）では
1回に1.38秒かかり、ログインが遅い原因になっていた。

Argon2id：OWASP（Web の安全の基準をまとめている団体）がいちばんに勧めている方式。
計算にメモリをたくさん使うので、データが盗まれても、専用の機械でまとめて総当たりするのが難しい。
設定は OWASP の Password Storage Cheat Sheet の「メモリ19MiB・2回・1並列」。
このパソコンでは1回0.05秒（PBKDF2 は1.02秒）。
Django の標準の Argon2 の設定（メモリ100MiB・8並列）は、無料プランの小さなサーバーには重いので使わない。
"""
from django.contrib.auth.hashers import Argon2PasswordHasher


class OwaspArgon2PasswordHasher(Argon2PasswordHasher):
    time_cost = 2  # 計算を何回くり返すか
    memory_cost = 19 * 1024  # 使うメモリ（KiB）。19MiB
    parallelism = 1  # 同時にいくつ並べて計算するか
