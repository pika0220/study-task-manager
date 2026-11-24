import io
import math
import re
import shutil
import tempfile
from datetime import datetime, timedelta
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.exceptions import FieldError, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.db import connection
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from PIL import Image
from pathlib import Path

from .encryption import decrypt_bytes, is_encrypted_text
from .models import Feedback, Source, Task, category_named
from .views import GUIDE_CHAPTERS



# タイトルや分類の名前は暗号化して保存している（Issue #49）ので、データベースでは「タイトルが○○」と探せない。
# テストでは、全部読んで Python の中でくらべる
def task_titled(title):
    """タイトルが title のタスクを1件返す（なければ DoesNotExist、2件以上なら MultipleObjectsReturned）。"""
    found = [task for task in Task.objects.all() if task.title == title]
    if not found:
        raise Task.DoesNotExist(title)
    if len(found) > 1:
        raise Task.MultipleObjectsReturned(title)
    return found[0]


def has_task(title):
    return any(task.title == title for task in Task.objects.all())


def named(user, name):
    """user の分類のうち、名前が name のもの（なければテストを失敗させる）。"""
    category = category_named(user, name)
    assert category is not None, f"分類「{name}」がありません"
    return category


class HomeViewTests(TestCase):
    def test_anonymous_user_is_sent_to_login(self):
        response = self.client.get(reverse("home"))
        self.assertRedirects(response, reverse("login") + "?next=/")

    def test_home_counts_only_my_open_tasks(self):
        User = get_user_model()
        me = User.objects.create_user("student", password="test-pass-123")
        other = User.objects.create_user("other", password="test-pass-123")
        Task.objects.create(owner=me, title="日誌を書く")
        Task.objects.create(owner=me, title="済んだ課題", is_done=True)
        Task.objects.create(owner=other, title="他の人のタスク")
        self.client.force_login(me)
        response = self.client.get(reverse("home"))
        # 画面に渡す数が「自分の未完了だけ」の1件になっている
        self.assertEqual(response.context["open_count"], 1)

    def test_home_lists_my_top_level_tasks(self):
        User = get_user_model()
        me = User.objects.create_user("student", password="test-pass-123")
        other = User.objects.create_user("other", password="test-pass-123")
        exam = Task.objects.create(owner=me, title="国家試験", importance=Task.Importance.HIGH)
        Task.objects.create(owner=me, title="薬理学の問題集", parent=exam)
        Task.objects.create(owner=me, title="レポート提出", is_done=True)
        Task.objects.create(owner=other, title="他の人のタスク")
        self.client.force_login(me)
        response = self.client.get(reverse("home"))

        self.assertEqual([t.title for t in response.context["open_tasks"]], ["国家試験"])
        self.assertEqual([t.title for t in response.context["done_tasks"]], ["レポート提出"])
        self.assertContains(response, "重要度 高")
        self.assertContains(response, "サブタスク 1件・0%")
        # サブタスクは別のカードにしない（上の open_tasks が1件）。中身は ▶ を押したときに読む（Issue #57）
        self.assertNotContains(response, ">薬理学の問題集</a>")
        children = self.client.get(reverse("task_children", args=[exam.pk]))
        self.assertContains(children, ">薬理学の問題集</a>", count=1)
        # ほかの人のタスクは出さない
        self.assertNotContains(response, "他の人のタスク")

    def test_home_orders_by_importance_then_due(self):
        me = get_user_model().objects.create_user("student", password="test-pass-123")
        now = timezone.now()
        Task.objects.create(owner=me, title="低・明日", importance=Task.Importance.LOW, due_at=now + timedelta(days=1))
        Task.objects.create(owner=me, title="高・来週", importance=Task.Importance.HIGH, due_at=now + timedelta(days=7))
        Task.objects.create(owner=me, title="高・明日", importance=Task.Importance.HIGH, due_at=now + timedelta(days=1))
        Task.objects.create(owner=me, title="高・締切なし", importance=Task.Importance.HIGH)
        self.client.force_login(me)
        response = self.client.get(reverse("home"))
        self.assertEqual(
            [t.title for t in response.context["open_tasks"]],
            ["高・明日", "高・来週", "高・締切なし", "低・明日"],
        )

    def test_home_shows_message_when_empty(self):
        me = get_user_model().objects.create_user("student", password="test-pass-123")
        self.client.force_login(me)
        response = self.client.get(reverse("home"))
        self.assertContains(response, "未完了のタスクはありません")



class TaskCreateViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.me = User.objects.create_user("student", password="test-pass-123")
        self.other = User.objects.create_user("other", password="test-pass-123")
        self.url = reverse("task_create")

    def test_anonymous_user_is_sent_to_login(self):
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("login") + "?next=" + self.url)

    def test_create_with_title_only(self):
        self.client.force_login(self.me)
        response = self.client.post(
            self.url, {"title": "実習記録を書く", "importance": 2}, follow=True
        )
        self.assertRedirects(response, reverse("home"))
        task = task_titled("実習記録を書く")
        self.assertEqual(task.owner, self.me)
        self.assertIsNone(task.due_at)
        # ホームに戻ると、お知らせとカードが出る
        self.assertContains(response, "「実習記録を書く」を登録しました")
        self.assertEqual([t.title for t in response.context["open_tasks"]], ["実習記録を書く"])

    def test_owner_cannot_be_changed_from_form(self):
        # フォームに owner を混ぜて送っても、持ち主はログイン中の本人になる
        self.client.force_login(self.me)
        self.client.post(
            self.url,
            {"title": "なりすまし", "importance": 2, "owner": self.other.pk},
        )
        self.assertEqual(task_titled("なりすまし").owner, self.me)

    def test_due_at_from_datetime_local_input(self):
        # スマホの日時入力（datetime-local）が送ってくる形で受け取れる
        self.client.force_login(self.me)
        self.client.post(
            self.url,
            {"title": "レポート", "importance": 3, "due_at": "2026-12-01T18:00"},
        )
        due = timezone.localtime(task_titled("レポート").due_at)
        self.assertEqual((due.month, due.day, due.hour, due.minute), (12, 1, 18, 0))

    def test_title_is_required(self):
        self.client.force_login(self.me)
        response = self.client.post(self.url, {"title": "", "importance": 2})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Task.objects.exists())
        self.assertContains(response, "このフィールドは必須です")


class TaskToggleViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.me = User.objects.create_user("student", password="test-pass-123")
        self.other = User.objects.create_user("other", password="test-pass-123")
        self.task = Task.objects.create(owner=self.me, title="実習記録を書く")

    def url(self, task):
        return reverse("task_toggle", args=[task.pk])

    def test_toggle_marks_done_then_undone(self):
        self.client.force_login(self.me)
        response = self.client.post(self.url(self.task), follow=True)
        self.assertRedirects(response, reverse("home"))
        self.task.refresh_from_db()  # データベースから読み直す
        self.assertTrue(self.task.is_done)
        self.assertIsNotNone(self.task.done_at)
        self.assertContains(response, "「実習記録を書く」を完了にしました")
        self.assertEqual([t.title for t in response.context["done_tasks"]], ["実習記録を書く"])

        # もう一度押すと未完了に戻る
        response = self.client.post(self.url(self.task), follow=True)
        self.task.refresh_from_db()
        self.assertFalse(self.task.is_done)
        self.assertIsNone(self.task.done_at)
        self.assertContains(response, "未完了に戻しました")

    def test_cannot_toggle_other_users_task(self):
        others = Task.objects.create(owner=self.other, title="他の人のタスク")
        self.client.force_login(self.me)
        response = self.client.post(self.url(others))
        self.assertEqual(response.status_code, 404)
        others.refresh_from_db()
        self.assertFalse(others.is_done)

    def test_get_is_not_allowed(self):
        # リンクを開いただけ（GET）では切り替わらない
        self.client.force_login(self.me)
        response = self.client.get(self.url(self.task))
        self.assertEqual(response.status_code, 405)
        self.task.refresh_from_db()
        self.assertFalse(self.task.is_done)

    def test_anonymous_user_is_sent_to_login(self):
        response = self.client.post(self.url(self.task))
        self.assertRedirects(response, reverse("login") + "?next=" + self.url(self.task))
        self.task.refresh_from_db()
        self.assertFalse(self.task.is_done)

    def test_home_shows_toggle_buttons(self):
        self.client.force_login(self.me)
        response = self.client.get(reverse("home"))
        self.assertContains(response, self.url(self.task))
        self.assertContains(response, "を完了にする")


class TaskDetailEditTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.me = User.objects.create_user("student", password="test-pass-123")
        self.other = User.objects.create_user("other", password="test-pass-123")
        self.exam = Task.objects.create(owner=self.me, title="国家試験")
        self.client.force_login(self.me)

    def test_detail_shows_task_and_children(self):
        Task.objects.create(owner=self.me, title="薬理学", parent=self.exam)
        response = self.client.get(reverse("task_detail", args=[self.exam.pk]))
        self.assertContains(response, "国家試験")
        self.assertContains(response, "サブタスク（1件）")
        self.assertContains(response, "薬理学")
        self.assertContains(response, reverse("task_create_child", args=[self.exam.pk]))

    def test_home_card_links_to_detail(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, reverse("task_detail", args=[self.exam.pk]))

    def test_other_users_task_is_404(self):
        others = Task.objects.create(owner=self.other, title="他の人のタスク")
        for name in ("task_detail", "task_update", "task_create_child"):
            response = self.client.get(reverse(name, args=[others.pk]))
            self.assertEqual(response.status_code, 404, name)
        # 編集を送っても変わらない
        self.client.post(reverse("task_update", args=[others.pk]), {"title": "書きかえ", "importance": 2})
        others.refresh_from_db()
        self.assertEqual(others.title, "他の人のタスク")

    def test_edit_task(self):
        url = reverse("task_update", args=[self.exam.pk])
        self.assertContains(self.client.get(url), 'value="国家試験"')  # 今の内容が入った状態で開く
        response = self.client.post(
            url, {"title": "看護師国家試験", "importance": 3, "due_at": "2027-02-14T09:00"}, follow=True
        )
        self.assertRedirects(response, reverse("task_detail", args=[self.exam.pk]))
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.title, "看護師国家試験")
        self.assertEqual(self.exam.importance, 3)
        self.assertEqual(self.exam.owner, self.me)
        self.assertContains(response, "を保存しました")

    def test_add_child_task(self):
        url = reverse("task_create_child", args=[self.exam.pk])
        # 詳細画面から開くと、親タスクの欄に最初からそのタスクが選ばれている
        self.assertContains(self.client.get(url), f'<option value="{self.exam.pk}" selected>')
        response = self.client.post(
            url, {"title": "薬理学", "parent": self.exam.pk, "importance": 2}, follow=True
        )
        self.assertRedirects(response, reverse("task_detail", args=[self.exam.pk]))
        child = task_titled("薬理学")
        self.assertEqual(child.parent, self.exam)
        self.assertEqual(child.owner, self.me)

    def test_cannot_add_fourth_level(self):
        subject = Task.objects.create(owner=self.me, title="薬理学", parent=self.exam)
        chapter = Task.objects.create(owner=self.me, title="第1章", parent=subject)
        detail = self.client.get(reverse("task_detail", args=[chapter.pk]))
        self.assertNotContains(detail, reverse("task_create_child", args=[chapter.pk]))
        self.assertContains(detail, "3段目 → 📁 サブタスクは付けられない")
        # URLを直接開いても作れず、詳細画面に戻される
        url = reverse("task_create_child", args=[chapter.pk])
        response = self.client.post(url, {"title": "4段目", "importance": 2})
        self.assertRedirects(response, reverse("task_detail", args=[chapter.pk]))
        self.assertFalse(has_task("4段目"))

    def test_third_level_task_cannot_be_chosen_as_parent(self):
        # 3段目のタスクは親の選択肢に出ず、番号を直接送っても「正しくない選択」になる
        from .forms import TaskForm
        subject = Task.objects.create(owner=self.me, title="薬理学", parent=self.exam)
        chapter = Task.objects.create(owner=self.me, title="第1章", parent=subject)
        form = TaskForm(
            data={"title": "4段目", "parent": chapter.pk, "importance": 2},
            instance=Task(owner=self.me),
            user=self.me,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("parent", form.errors)

    def test_toggle_returns_to_detail_page(self):
        detail_url = reverse("task_detail", args=[self.exam.pk])
        response = self.client.post(reverse("task_toggle", args=[self.exam.pk]), {"next": detail_url})
        self.assertRedirects(response, detail_url)

    def test_toggle_ignores_outside_next(self):
        # ほかのサイトのURLを next に入れられても、ホームに戻す
        response = self.client.post(reverse("task_toggle", args=[self.exam.pk]), {"next": "https://example.com/"})
        self.assertRedirects(response, reverse("home"))


class CardChildrenTests(TestCase):
    def setUp(self):
        self.me = get_user_model().objects.create_user("student", password="test-pass-123")
        self.client.force_login(self.me)

    def test_card_lists_children_and_grandchildren(self):
        exam = Task.objects.create(owner=self.me, title="国家試験")
        subject = Task.objects.create(owner=self.me, title="薬理学", parent=exam)
        Task.objects.create(owner=self.me, title="問題集 第1章", parent=subject, is_done=True)
        response = self.client.get(reverse("home"))
        # ホームのカードには ▶ の行（数と進み具合）だけ。中身は ▶ を押したときに読む（Issue #57）
        self.assertContains(response, "サブタスク 1件・0%")
        self.assertContains(response, "<details", count=1)
        self.assertContains(response, reverse("task_children", args=[exam.pk]))
        self.assertNotContains(response, "問題集 第1章")
        # ▶ を開くと、サブタスクの行（切り替えボタン・詳細へのリンク・その下の ▶）が来る
        children = self.client.get(reverse("task_children", args=[exam.pk]))
        self.assertContains(children, "薬理学")
        self.assertContains(children, "サブタスク 1件・100%")
        self.assertContains(children, reverse("task_toggle", args=[subject.pk]))
        self.assertContains(children, reverse("task_detail", args=[subject.pk]))
        self.assertContains(children, reverse("task_children", args=[subject.pk]))
        # 孫は、薬理学の ▶ を開いたときに来る
        self.assertContains(self.client.get(reverse("task_children", args=[subject.pk])), "問題集 第1章")

    def test_card_without_children_has_no_toggle(self):
        Task.objects.create(owner=self.me, title="履修登録")
        response = self.client.get(reverse("home"))
        self.assertNotContains(response, "<details")

    def test_home_query_count_does_not_grow_with_children(self):
        # 子タスクが増えても、データベースへの問い合わせの回数は増えない（まとめて読み込んでいる）
        exam = Task.objects.create(owner=self.me, title="国家試験")
        for i in range(5):
            sub = Task.objects.create(owner=self.me, title=f"科目{i}", parent=exam)
            Task.objects.create(owner=self.me, title=f"章{i}", parent=sub)
        self.client.get(reverse("home"))  # 1回目はセッションなどの準備があるので数えない
        with self.assertNumQueries(5):  # サブタスクの中身は ▶ で読むので、1段目の分だけ（子タスクの数では増えない）
            self.client.get(reverse("home"))


class ParentSelectTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.me = User.objects.create_user("student", password="test-pass-123")
        self.other = User.objects.create_user("other", password="test-pass-123")
        self.exam = Task.objects.create(owner=self.me, title="国家試験")
        self.subject = Task.objects.create(owner=self.me, title="薬理学", parent=self.exam)
        self.chapter = Task.objects.create(owner=self.me, title="第1章", parent=self.subject)
        self.done = Task.objects.create(owner=self.me, title="履修登録", is_done=True)
        self.others = Task.objects.create(owner=self.other, title="他の人のタスク")
        self.client.force_login(self.me)

    def choices(self, response):
        return [label for value, label in response.context["form"].fields["parent"].choices]

    def test_create_form_lists_parents_as_tree(self):
        labels = self.choices(self.client.get(reverse("task_create")))
        # 親の下に子が字下げされて並ぶ。3段目・完了・他の人のタスクは出ない
        self.assertEqual(labels, ["なし（いちばん上のタスク）", "国家試験", "　└ 薬理学"])

    def test_create_child_from_menu(self):
        response = self.client.post(
            reverse("task_create"),
            {"title": "問題集 第2章", "parent": self.subject.pk, "importance": 2},
        )
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(task_titled("問題集 第2章").parent, self.subject)

    def test_cannot_choose_other_users_task(self):
        response = self.client.post(
            reverse("task_create"),
            {"title": "なりすまし", "parent": self.others.pk, "importance": 2},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(has_task("なりすまし"))

    def test_edit_hides_self_descendants_and_too_deep(self):
        # 薬理学（下に1段ある）を編集するとき：自分と子孫は出ない。2段目の下に付けると4段になるので出ない
        other_top = Task.objects.create(owner=self.me, title="卒業研究")
        Task.objects.create(owner=self.me, title="文献調査", parent=other_top)
        labels = self.choices(self.client.get(reverse("task_update", args=[self.subject.pk])))
        self.assertEqual(labels, ["なし（いちばん上のタスク）", "国家試験", "卒業研究"])

    def test_move_task_to_another_parent(self):
        other_top = Task.objects.create(owner=self.me, title="卒業研究")
        self.client.post(
            reverse("task_update", args=[self.chapter.pk]),
            {"title": "第1章", "parent": other_top.pk, "importance": 2},
        )
        self.chapter.refresh_from_db()
        self.assertEqual(self.chapter.parent, other_top)

    def test_add_child_link_inside_card(self):
        # ▶ を開いたときに読む行の並び（Issue #57）：1段目・2段目の中には「＋ サブタスク」があり、3段目の下にはない
        def opened(task):
            return self.client.get(reverse("task_children", args=[task.pk]))

        self.assertContains(opened(self.exam), reverse("task_create_child", args=[self.exam.pk]))
        self.assertContains(opened(self.subject), reverse("task_create_child", args=[self.subject.pk]))
        self.assertNotContains(opened(self.chapter), reverse("task_create_child", args=[self.chapter.pk]))


class CategoryTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.me = User.objects.create_user("student", password="test-pass-123")
        self.other = User.objects.create_user("other", password="test-pass-123")
        self.client.force_login(self.me)

    def test_new_user_gets_default_categories(self):
        self.assertEqual(
            list(self.me.categories.values_list("name", flat=True)), ["授業", "実習", "資格対策", "研究"]
        )

    def test_form_lists_only_my_categories_and_new_option(self):
        self.other.categories.create(name="他の人の分類")
        response = self.client.get(reverse("task_create"))
        labels = [label for value, label in response.context["form"].fields["category"].choices]
        self.assertEqual(labels, ["分類なし", "授業", "実習", "資格対策", "研究", "＋ 新しい分類…"])

    def test_create_task_with_existing_category(self):
        practicum = named(self.me, "実習")
        self.client.post(reverse("task_create"), {"title": "実習記録", "importance": 2, "category": practicum.pk})
        self.assertEqual(task_titled("実習記録").category, practicum)

    def test_create_task_with_new_category(self):
        self.client.post(
            reverse("task_create"),
            {"title": "大会の準備", "importance": 2, "category": "__new__", "new_category_name": " 部活 "},
        )
        task = task_titled("大会の準備")
        self.assertEqual(task.category.name, "部活")  # 前後の空白は取る
        self.assertEqual(task.category.owner, self.me)
        # 次からは選択肢に並ぶ
        labels = [l for v, l in self.client.get(reverse("task_create")).context["form"].fields["category"].choices]
        self.assertIn("部活", labels)

    def test_new_category_with_existing_name_reuses_it(self):
        self.client.post(
            reverse("task_create"),
            {"title": "日誌", "importance": 2, "category": "__new__", "new_category_name": "実習"},
        )
        self.assertEqual(sum(c.name == "実習" for c in self.me.categories.all()), 1)
        self.assertEqual(task_titled("日誌").category.name, "実習")

    def test_new_category_needs_a_name(self):
        response = self.client.post(
            reverse("task_create"), {"title": "日誌", "importance": 2, "category": "__new__", "new_category_name": ""}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "新しい分類の名前を入れてください")
        self.assertFalse(Task.objects.exists())

    def test_new_category_not_created_when_other_field_invalid(self):
        self.client.post(
            reverse("task_create"), {"title": "", "importance": 2, "category": "__new__", "new_category_name": "部活"}
        )
        self.assertFalse((category_named(self.me, "部活") is not None))

    def test_cannot_use_other_users_category(self):
        others = named(self.other, "授業")
        response = self.client.post(reverse("task_create"), {"title": "なりすまし", "importance": 2, "category": others.pk})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(has_task("なりすまし"))

    def test_card_shows_category_name(self):
        Task.objects.create(owner=self.me, title="日誌", category=named(self.me, "実習"))
        Task.objects.create(owner=self.me, title="分類なしのタスク")
        response = self.client.get(reverse("home"))
        self.assertContains(response, "実習")
        self.assertContains(response, "分類なし")

    def test_rename_category(self):
        cls = named(self.me, "授業")
        self.client.post(reverse("category_update", args=[cls.pk]), {"name": "講義"})
        cls.refresh_from_db()
        self.assertEqual(cls.name, "講義")

    def test_rename_to_existing_name_is_rejected(self):
        cls = named(self.me, "授業")
        response = self.client.post(reverse("category_update", args=[cls.pk]), {"name": "実習"}, follow=True)
        cls.refresh_from_db()
        self.assertEqual(cls.name, "授業")
        self.assertContains(response, "分類「実習」はもうあります")

    def test_delete_category_keeps_tasks(self):
        research = named(self.me, "研究")
        task = Task.objects.create(owner=self.me, title="文献調査", category=research)
        response = self.client.post(reverse("category_delete", args=[research.pk]), follow=True)
        self.assertContains(response, "1件のタスクは「分類なし」になりました")
        task.refresh_from_db()
        self.assertIsNone(task.category)

    def test_create_category_on_manage_page(self):
        self.client.post(reverse("category_create"), {"name": "バイト"})
        self.assertTrue((category_named(self.me, "バイト") is not None))

    def test_cannot_change_other_users_category(self):
        others = named(self.other, "授業")
        self.assertEqual(self.client.post(reverse("category_update", args=[others.pk]), {"name": "x"}).status_code, 404)
        self.assertEqual(self.client.post(reverse("category_delete", args=[others.pk])).status_code, 404)
        self.assertTrue((category_named(self.other, "授業") is not None))

    def test_manage_page_lists_my_categories_with_counts(self):
        Task.objects.create(owner=self.me, title="日誌", category=named(self.me, "実習"))
        response = self.client.get(reverse("category_list"))
        self.assertContains(response, 'value="実習"')
        self.assertContains(response, "タスク 1件")


class SourceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.me = User.objects.create_user("student", password="test-pass-123")
        self.other = User.objects.create_user("other", password="test-pass-123")
        self.task = Task.objects.create(owner=self.me, title="実習記録")
        self.client.force_login(self.me)
        self.url = reverse("source_create", args=[self.task.pk])

    def test_add_source_with_link(self):
        response = self.client.post(
            self.url, {"kind": "classroom", "url": "https://classroom.example.com/c/123", "memo": ""}, follow=True
        )
        self.assertRedirects(response, reverse("task_detail", args=[self.task.pk]))
        source = self.task.sources.get()
        self.assertEqual(source.kind, "classroom")
        # 別のタブで開き、開いた先から操作されない設定が付いている
        self.assertContains(response, 'href="https://classroom.example.com/c/123" target="_blank" rel="noopener noreferrer"')

    def test_form_explains_link_or_memo(self):
        response = self.client.get(reverse("task_detail", args=[self.task.pk]))
        self.assertContains(response, "リンク・メモ・写真 → 1つ以上（いくつでも）")
        self.assertNotContains(response, "なくてもよい）</legend>")

    def test_add_source_with_memo_only(self):
        self.client.post(self.url, {"kind": "paper", "url": "", "memo": "3限の配布プリント"})
        self.assertEqual(self.task.sources.get().memo, "3限の配布プリント")

    def test_link_without_scheme_becomes_https(self):
        self.client.post(self.url, {"kind": "slide", "url": "example.com/slide", "memo": ""})
        self.assertEqual(self.task.sources.get().url, "https://example.com/slide")

    def test_empty_source_is_rejected(self):
        response = self.client.post(self.url, {"kind": "paper", "url": "", "memo": "  "}, follow=True)
        self.assertFalse(Source.objects.exists())
        self.assertContains(response, "リンク・メモ・写真のどれか1つ以上を入れてください")

    def test_dangerous_link_is_rejected(self):
        for bad in ("javascript:alert(1)", "ftp://example.com/file"):
            self.client.post(self.url, {"kind": "other", "url": bad, "memo": "x"})
        self.assertFalse(Source.objects.exists())

    def test_cannot_add_to_other_users_task(self):
        others = Task.objects.create(owner=self.other, title="他の人のタスク")
        response = self.client.post(reverse("source_create", args=[others.pk]), {"kind": "paper", "memo": "x"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Source.objects.exists())

    def test_delete_source(self):
        source = Source.objects.create(task=self.task, kind="paper", memo="プリント")
        response = self.client.post(reverse("source_delete", args=[source.pk]))
        self.assertRedirects(response, reverse("task_detail", args=[self.task.pk]))
        self.assertFalse(Source.objects.exists())

    def test_cannot_delete_other_users_source(self):
        others = Task.objects.create(owner=self.other, title="他の人のタスク")
        source = Source.objects.create(task=others, kind="paper", memo="プリント")
        self.assertEqual(self.client.post(reverse("source_delete", args=[source.pk])).status_code, 404)
        self.assertTrue(Source.objects.filter(pk=source.pk).exists())

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_card_shows_clip_count(self):
        Source.objects.create(task=self.task, kind="paper", memo="プリント")
        Source.objects.create(task=self.task, kind="slack", memo="お知らせ")
        self.assertContains(self.client.get(reverse("home")), "📎 2")

    def test_sources_are_deleted_with_task(self):
        Source.objects.create(task=self.task, kind="paper", memo="プリント")
        self.task.delete()
        self.assertFalse(Source.objects.exists())


TEMP_MEDIA = tempfile.mkdtemp()  # テストでは写真を一時フォルダに保存し、終わったら消す


def make_image(fmt="JPEG", size=(3000, 1500), exif=None, name=None):
    buffer = io.BytesIO()
    image = Image.new("RGB", size, (200, 30, 30))
    kwargs = {"exif": exif} if exif is not None else {}
    image.save(buffer, format=fmt, **kwargs)
    ext = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif"}[fmt]
    return SimpleUploadedFile(name or f"photo.{ext}", buffer.getvalue(), content_type=f"image/{ext}")


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class SourcePhotoTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        User = get_user_model()
        self.me = User.objects.create_user("student", password="test-pass-123")
        self.other = User.objects.create_user("other", password="test-pass-123")
        self.task = Task.objects.create(owner=self.me, title="実習記録")
        self.client.force_login(self.me)
        self.url = reverse("source_create", args=[self.task.pk])

    def post_photo(self, upload, **extra):
        data = {"kind": "paper", "url": "", "memo": "", "photo": upload}
        data.update(extra)
        return self.client.post(self.url, data, follow=True)

    def test_upload_photo_only(self):
        response = self.post_photo(make_image())
        self.assertContains(response, "資料を追加しました")
        source = self.task.sources.get()
        # ランダムな名前のJPEGとして、持ち主のフォルダに保存される
        self.assertRegex(source.photo.name, rf"^source_photos/{self.me.pk}/[0-9a-f]{{32}}\.jpg$")
        with source.photo.open("rb") as file, Image.open(file) as saved:  # 暗号化してあるので、open() で元に戻して読む
            self.assertEqual(saved.format, "JPEG")
            self.assertEqual(max(saved.size), 2000)  # 長い辺は2000pxまで縮める
        self.assertContains(response, reverse("source_photo", args=[source.pk]))

    def test_exif_is_removed(self):
        exif = Image.Exif()
        exif[0x010F] = "TestCameraMaker"  # 機種の情報
        exif[0x0132] = "2026:11:20 10:00:00"  # 撮影日時
        self.post_photo(make_image(exif=exif))
        with self.task.sources.get().photo.open("rb") as file, Image.open(file) as saved:  # 暗号化してあるので、open() で元に戻して読む
            self.assertEqual(len(saved.getexif()), 0)

    def test_png_and_webp_are_accepted(self):
        self.post_photo(make_image("PNG", size=(400, 300)))
        self.post_photo(make_image("WEBP", size=(400, 300)))
        self.assertEqual(self.task.sources.count(), 2)

    def test_fake_image_is_rejected(self):
        fake = SimpleUploadedFile("evil.jpg", b"<script>alert(1)</script>", content_type="image/jpeg")
        response = self.post_photo(fake)
        self.assertContains(response, "画像として読み込めないファイルです")
        self.assertFalse(Source.objects.exists())

    def test_other_format_is_rejected(self):
        response = self.post_photo(make_image("GIF", size=(50, 50)))
        self.assertContains(response, "JPEG・PNG・WebPの写真だけをのせられます")
        self.assertFalse(Source.objects.exists())

    def test_too_large_file_is_rejected(self):
        big = SimpleUploadedFile("big.jpg", b"0" * (10 * 1024 * 1024 + 1), content_type="image/jpeg")
        response = self.post_photo(big)
        self.assertContains(response, "写真は10MBまでにしてください")
        self.assertFalse(Source.objects.exists())

    def test_too_many_pixels_is_rejected(self):
        # ファイルは小さいが、開くと巨大になる画像（解凍爆弾）
        buffer = io.BytesIO()
        Image.new("1", (8000, 7000)).save(buffer, format="PNG")
        bomb = SimpleUploadedFile("bomb.png", buffer.getvalue(), content_type="image/png")
        response = self.post_photo(bomb)
        self.assertContains(response, "写真の画素数が大きすぎます")
        self.assertFalse(Source.objects.exists())

    def test_photo_is_visible_only_to_owner(self):
        self.post_photo(make_image(size=(100, 100)))
        source = self.task.sources.get()
        url = reverse("source_photo", args=[source.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertIn("private", response["Cache-Control"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        # ほかの人は見られない
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)
        # ログインしていない人はログイン画面へ
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_photo_file_is_deleted_with_source_and_task(self):
        self.post_photo(make_image(size=(100, 100)))
        source = self.task.sources.get()
        path = source.photo.path
        self.client.post(reverse("source_delete", args=[source.pk]))
        self.assertFalse(Path(path).exists())
        # タスクを消したときも、写真のファイルが残らない
        self.post_photo(make_image(size=(100, 100)))
        path = self.task.sources.get().photo.path
        self.task.delete()
        self.assertFalse(Path(path).exists())

    def test_photo_limit_per_user(self):
        # 上限を一時的に1枚にして確かめる
        original = Source.MAX_PHOTOS_PER_USER
        Source.MAX_PHOTOS_PER_USER = 1
        try:
            self.post_photo(make_image(size=(100, 100)))
            response = self.post_photo(make_image(size=(100, 100)))
        finally:
            Source.MAX_PHOTOS_PER_USER = original
        self.assertContains(response, "写真は1人1枚までです")
        self.assertEqual(self.task.sources.count(), 1)

    def test_form_has_privacy_notice_and_multipart(self):
        response = self.client.get(reverse("task_detail", args=[self.task.pk]))
        self.assertContains(response, "個人情報（患者さん・利用者さんの名前など）が写っている写真はのせないでください")
        self.assertContains(response, 'enctype="multipart/form-data"')


class PrerequisiteTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.me = User.objects.create_user("student", password="test-pass-123")
        self.other = User.objects.create_user("other", password="test-pass-123")
        self.research = Task.objects.create(owner=self.me, title="卒業研究")
        self.survey = Task.objects.create(owner=self.me, title="文献調査", parent=self.research)
        self.outline = Task.objects.create(owner=self.me, title="構成を考える", parent=self.research)
        self.client.force_login(self.me)

    def labels(self, url):
        return [label for value, label in self.client.get(url).context["form"].fields["prerequisite"].choices]

    def test_set_prerequisite_from_edit(self):
        self.client.post(
            reverse("task_update", args=[self.outline.pk]),
            {"title": "構成を考える", "parent": self.research.pk, "prerequisite": self.survey.pk, "importance": 2},
        )
        self.outline.refresh_from_db()
        self.assertEqual(self.outline.prerequisite, self.survey)
        self.assertTrue(self.outline.is_locked)

    def test_choices_show_path_and_hide_invalid(self):
        Task.objects.create(owner=self.me, title="済んだタスク", is_done=True)
        Task.objects.create(owner=self.other, title="他の人のタスク")
        labels = self.labels(reverse("task_update", args=[self.outline.pk]))
        # 親（卒業研究）・自分・完了・他の人のタスクは出ない。道すじ付きの見出しで出る
        self.assertEqual(labels, ["なし", "卒業研究 › 文献調査"])

    def test_choices_hide_tasks_that_would_make_a_loop(self):
        self.outline.prerequisite = self.survey
        self.outline.save()
        # 文献調査を編集するとき、文献調査のあとにやる「構成を考える」は選べない（輪になる）
        labels = self.labels(reverse("task_update", args=[self.survey.pk]))
        self.assertNotIn("卒業研究 › 構成を考える", labels)

    def test_model_rejects_loop_self_parent_and_child(self):
        self.outline.prerequisite = self.survey
        self.outline.save()
        cases = [
            (self.survey, self.outline, "輪になってしまいます"),
            (self.survey, self.survey, "自分自身は選べません"),
            (self.survey, self.research, "自分が入っている上のタスクは選べません"),
            (self.research, self.survey, "自分のサブタスクは選べません"),
        ]
        for task, prereq, message in cases:
            task.prerequisite = prereq
            with self.assertRaises(ValidationError) as ctx:
                task.full_clean()
            self.assertIn(message, " ".join(ctx.exception.message_dict["prerequisite"]))
            task.refresh_from_db()

    def test_cannot_choose_other_users_task(self):
        others = Task.objects.create(owner=self.other, title="他の人のタスク")
        response = self.client.post(
            reverse("task_create"), {"title": "なりすまし", "prerequisite": others.pk, "importance": 2}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(has_task("なりすまし"))

    def test_locked_card_and_unlock_after_done(self):
        report = Task.objects.create(owner=self.me, title="レポートを書く", prerequisite=self.research)
        home = self.client.get(reverse("home"))
        self.assertContains(home, "🔒 「卒業研究」が終わってから")
        # 先にやることを完了にすると、🔒 が消える
        self.client.post(reverse("task_toggle", args=[self.research.pk]))
        home = self.client.get(reverse("home"))
        self.assertNotContains(home, "🔒 「卒業研究」が終わってから")
        report.refresh_from_db()
        self.assertFalse(report.is_locked)

    def test_home_puts_available_tasks_first(self):
        Task.objects.create(owner=self.me, title="高・待ち", importance=3, prerequisite=self.research)
        Task.objects.create(owner=self.me, title="低・すぐできる", importance=1)
        titles = [t.title for t in self.client.get(reverse("home")).context["open_tasks"]]
        # 重要度が高くても、🔒 のタスクはすぐできるタスクより下
        self.assertLess(titles.index("低・すぐできる"), titles.index("高・待ち"))

    def test_deleting_prerequisite_keeps_task(self):
        report = Task.objects.create(owner=self.me, title="レポートを書く", prerequisite=self.survey)
        self.survey.delete()
        report.refresh_from_db()
        self.assertIsNone(report.prerequisite)

    def test_form_groups_relations_with_question_labels(self):
        response = self.client.get(reverse("task_create"))
        html = response.content.decode()
        self.assertIn("つながり（なくてもよい）", html)
        self.assertIn("📁 どのタスクのサブタスク？", html)
        self.assertIn("🔒 何が終わってから取りかかる？", html)
        # 2つの質問は「つながり」の枠の中に並び、締切の欄より前にある
        self.assertLess(html.index("つながり（なくてもよい）"), html.index("📁 どのタスクのサブタスク？"))
        self.assertLess(html.index("🔒 何が終わってから取りかかる？"), html.index('id="field-due_at"'))

    def test_detail_shows_relation_diagram(self):
        self.outline.prerequisite = self.survey
        self.outline.save()
        response = self.client.get(reverse("task_detail", args=[self.outline.pk]))
        self.assertContains(response, "「<a href=\"%s\" class=\"link\">卒業研究</a>」のサブタスク" % reverse("task_detail", args=[self.research.pk]), html=False)
        self.assertContains(response, "このタスク")
        self.assertContains(response, "🔒 まだ")
        # つながりがないタスクには案内を出す
        response = self.client.get(reverse("task_detail", args=[self.research.pk]))
        self.assertContains(response, "🔒 なし（編集 → 🔒 何が終わってから取りかかる？）")
        self.assertContains(response, "（サブタスク 2件）")

    def test_detail_shows_prerequisite_and_next(self):
        self.outline.prerequisite = self.survey
        self.outline.save()
        response = self.client.get(reverse("task_detail", args=[self.survey.pk]))
        self.assertContains(response, "次にやること")
        self.assertContains(response, "構成を考える")
        response = self.client.get(reverse("task_detail", args=[self.outline.pk]))
        self.assertContains(response, "🔒 まだ")


class DragLinkTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.me = User.objects.create_user("student", password="test-pass-123")
        self.other = User.objects.create_user("other", password="test-pass-123")
        self.exam = Task.objects.create(owner=self.me, title="国家試験")
        self.subject = Task.objects.create(owner=self.me, title="薬理学", parent=self.exam)
        self.chapter = Task.objects.create(owner=self.me, title="第1章", parent=self.subject)
        self.report = Task.objects.create(owner=self.me, title="レポート")
        self.client.force_login(self.me)

    def options(self, source, target):
        url = reverse("task_link_options", args=[source.pk])
        return self.client.get(url, {"target": target.pk}).json()

    def link(self, source, target, relation, **extra):
        data = {"target": target.pk, "relation": relation}
        data.update(extra)
        return self.client.post(reverse("task_link", args=[source.pk]), data, follow=True)

    def test_options_for_a_valid_pair(self):
        data = self.options(self.report, self.subject)
        self.assertEqual(data["source"], "レポート")
        self.assertEqual(data["target"], "薬理学")
        self.assertTrue(data["child"]["ok"])
        self.assertTrue(data["after"]["ok"])

    def test_options_explain_why_not(self):
        # 3段目（第1章）の中には入れられない／自分の子孫（薬理学）の中には入れられない
        self.assertIn("3段まで", self.options(self.report, self.chapter)["child"]["reason"])
        data = self.options(self.exam, self.subject)
        self.assertFalse(data["child"]["ok"])
        self.assertIn("サブタスクは選べません", data["after"]["reason"])
        # 同じタスク同士
        same = self.options(self.report, self.report)
        self.assertEqual(same["child"]["reason"], "同じタスクです．")

    def test_link_as_child(self):
        response = self.link(self.report, self.exam, "child")
        self.assertContains(response, "「レポート」を「国家試験」のサブタスクにしました")
        self.report.refresh_from_db()
        self.assertEqual(self.report.parent, self.exam)

    def test_link_as_after(self):
        response = self.link(self.report, self.subject, "after")
        self.assertContains(response, "「レポート」は「薬理学」が終わってからにしました")
        self.report.refresh_from_db()
        self.assertEqual(self.report.prerequisite, self.subject)
        self.assertTrue(self.report.is_locked)

    def test_invalid_link_is_not_saved(self):
        response = self.link(self.report, self.chapter, "child")
        self.assertContains(response, "3段まで")
        self.report.refresh_from_db()
        self.assertIsNone(self.report.parent)
        # 輪になる前後
        self.report.prerequisite = self.exam
        self.report.save()
        self.link(self.exam, self.report, "after")
        self.exam.refresh_from_db()
        self.assertIsNone(self.exam.prerequisite)

    def test_unknown_relation(self):
        response = self.link(self.report, self.exam, "sibling")
        self.assertContains(response, "つなぎ方を選んでください")

    def test_other_users_tasks_are_404(self):
        others = Task.objects.create(owner=self.other, title="他の人のタスク")
        self.assertEqual(
            self.client.get(reverse("task_link_options", args=[self.report.pk]), {"target": others.pk}).status_code, 404
        )
        response = self.client.post(reverse("task_link", args=[others.pk]), {"target": self.report.pk, "relation": "child"})
        self.assertEqual(response.status_code, 404)
        response = self.client.post(reverse("task_link", args=[self.report.pk]), {"target": others.pk, "relation": "child"})
        self.assertEqual(response.status_code, 404)
        self.report.refresh_from_db()
        self.assertIsNone(self.report.parent)

    def test_bad_target_value_is_404(self):
        url = reverse("task_link_options", args=[self.report.pk])
        self.assertEqual(self.client.get(url, {"target": "abc"}).status_code, 404)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_get_is_not_allowed_for_saving(self):
        self.assertEqual(self.client.get(reverse("task_link", args=[self.report.pk])).status_code, 405)

    def test_returns_to_next_page(self):
        detail = reverse("task_detail", args=[self.exam.pk])
        response = self.client.post(
            reverse("task_link", args=[self.report.pk]), {"target": self.exam.pk, "relation": "child", "next": detail}
        )
        self.assertRedirects(response, detail)

    def test_cards_are_draggable_and_dialog_is_present(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, f'data-task-id="{self.report.pk}" data-title="レポート"')
        self.assertContains(response, 'id="link-dialog"')
        self.assertContains(response, "tasks/drag_link.js")
        self.assertContains(response, "カードを長押し → 別のカードに重ねる")


class MindMapTests(TestCase):
    """マインドマップ（Issue #30・#36）。分類ごとの部屋・棚・並べ方・置き場所の保存・名前だけで追加・つながりの切り替えと外す。"""

    def setUp(self):
        User = get_user_model()
        self.me = User.objects.create_user("student", password="test-pass-123")
        self.other = User.objects.create_user("other", password="test-pass-123")
        cats = {c.name: c for c in self.me.categories.all()}
        self.research, self.lesson = cats["研究"], cats["授業"]
        self.study = Task.objects.create(
            owner=self.me, title="卒業研究", importance=Task.Importance.HIGH, category=self.research, on_map=True
        )
        self.reading = Task.objects.create(owner=self.me, title="文献調査", parent=self.study)
        self.slides = Task.objects.create(
            owner=self.me, title="発表スライド", parent=self.study, prerequisite=self.reading
        )
        self.done_child = Task.objects.create(owner=self.me, title="済んだタスク", parent=self.study, is_done=True)
        self.paper = Task.objects.create(owner=self.me, title="論文を読む", category=self.research)  # 棚にある
        self.report = Task.objects.create(owner=self.me, title="実習レポート", category=self.lesson, on_map=True)
        self.others_task = Task.objects.create(owner=self.other, title="他の人のタスク")
        self.client.force_login(self.me)

    def room_url(self, category=None):
        key = str((category or self.research).pk) if category is not False else "none"
        return reverse("task_map_room", args=[key])

    def get_room(self, category=None):
        return self.client.get(self.room_url(category))

    def nodes(self, category=None):
        return {node["title"]: node for node in self.get_room(category).context["nodes"]}

    def center(self, node):
        return (node["x"] + 65, node["y"] + 22)  # 点の大きさ 130×44 の真ん中

    # ---- 部屋・切り替え ----
    def test_anonymous_user_is_sent_to_login(self):
        self.client.logout()
        self.assertRedirects(self.client.get(reverse("task_map")), reverse("login") + "?next=/map/")

    def test_map_top_opens_a_room_with_placed_tasks(self):
        response = self.client.get(reverse("task_map"))
        self.assertRedirects(response, self.room_url())  # 置いてあるタスクのある部屋（いちばん上の順で最初）

    def test_room_shows_only_placed_tasks_and_their_open_work(self):
        nodes = self.nodes()
        # 棚のタスク・終わったタスク・別の分類・他の人のタスクは出ない
        self.assertEqual(set(nodes), {"卒業研究", "文献調査", "発表スライド"})
        self.assertTrue(nodes["卒業研究"]["top"])
        self.assertFalse(nodes["文献調査"]["top"])
        self.assertEqual(nodes["文献調査"]["parent"], self.study.pk)
        self.assertEqual(nodes["発表スライド"]["prerequisite"], self.reading.pk)
        self.assertTrue(nodes["発表スライド"]["locked"])
        # 詳細画面の「←」でこの部屋に戻れるように、戻り先（back）が付いている
        self.assertEqual(
            nodes["卒業研究"]["url"],
            reverse("task_detail", args=[self.study.pk]) + "?" + urlencode({"back": self.room_url()}),
        )

    def test_room_menu_lists_categories_with_counts(self):
        response = self.get_room()
        rooms = {r["name"]: r["count"] for r in response.context["rooms"]}
        self.assertEqual(rooms["研究"], 2)  # 卒業研究（置いてある）＋論文を読む（棚）
        self.assertEqual(rooms["授業"], 1)
        self.assertNotIn("分類なし", rooms)  # 分類なしのタスクがないときは出さない
        self.assertContains(response, self.room_url(self.lesson))

    def test_no_category_room(self):
        Task.objects.create(owner=self.me, title="分類なしのメモ", on_map=True)
        self.assertEqual(set(self.nodes(False)), {"分類なしのメモ"})

    def test_cannot_open_others_room(self):
        other_category = self.other.categories.first()
        self.assertEqual(self.client.get(self.room_url(other_category)).status_code, 404)
        self.assertEqual(self.client.get(reverse("task_map_room", args=["abc"])).status_code, 404)

    def test_task_url_moves_to_its_room(self):
        response = self.client.get(reverse("task_map_root", args=[self.reading.pk]))
        self.assertRedirects(response, self.room_url())
        self.assertEqual(self.client.get(reverse("task_map_root", args=[self.others_task.pk])).status_code, 404)

    def test_links_to_tasks_outside_the_room_are_not_drawn(self):
        self.reading.prerequisite = self.report  # 別の部屋のタスク
        self.reading.save()
        self.assertIsNone(self.nodes()["文献調査"]["prerequisite"])

    # ---- 棚 ----
    def test_shelf_lists_unplaced_top_tasks(self):
        response = self.get_room()
        self.assertEqual([t.title for t in response.context["shelf"]], ["論文を読む"])
        self.assertEqual([t.title for t in response.context["placed"]], ["卒業研究"])
        self.assertContains(response, reverse("task_map_place", args=[self.paper.pk]))

    def test_place_puts_a_task_and_its_work_on_the_map(self):
        child = Task.objects.create(owner=self.me, title="1章", parent=self.paper)
        response = self.client.post(reverse("task_map_place", args=[self.paper.pk]), follow=True)
        # ?focus=番号：置いた木が画面の外にならないよう、画面がそこへ動く（task_map.js）
        self.assertRedirects(response, self.room_url() + f"?focus={self.paper.pk}")
        self.assertContains(response, "「論文を読む」を置きました．")
        nodes = self.nodes()
        self.assertIn("論文を読む", nodes)
        self.assertEqual(nodes["1章"]["parent"], self.paper.pk)
        self.assertEqual(child.pk, nodes["1章"]["id"])

    def test_second_placed_tree_goes_below_the_first(self):
        # サブタスクが上と下に出る木（上に出る分も考えて、重ならない場所に置けるか）
        Task.objects.create(owner=self.me, title="1章", parent=self.paper)
        Task.objects.create(owner=self.me, title="2章", parent=self.paper)
        first_tree = self.nodes()
        self.client.post(reverse("task_map_place", args=[self.paper.pk]))
        nodes = self.nodes()
        # スマホの縦長の画面に合うよう、2つ目の木は1つ目の木のすべての点より下に、縦に積む（横はそろえる）
        lowest = max(n["y"] for n in first_tree.values())
        for title in ["論文を読む", "1章", "2章"]:
            self.assertGreater(nodes[title]["y"], lowest + 44, title)
        self.assertEqual(nodes["論文を読む"]["x"], nodes["卒業研究"]["x"])

    def test_stow_returns_a_task_to_the_shelf(self):
        response = self.client.post(reverse("task_map_stow", args=[self.study.pk]), follow=True)
        self.assertContains(response, "「卒業研究」を棚にしまいました．")
        self.assertEqual(self.nodes(), {})
        self.assertTrue(Task.objects.filter(pk=self.reading.pk, parent=self.study).exists())  # つながりは残る

    def test_place_and_stow_only_top_tasks_of_mine_by_post(self):
        self.assertEqual(self.client.post(reverse("task_map_place", args=[self.reading.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("task_map_place", args=[self.others_task.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("task_map_stow", args=[self.study.pk])).status_code, 405)

    # ---- 並べ方 ----
    def test_work_spreads_around_its_top_task(self):
        self.slides.prerequisite = None  # 前後のつながりがない子が2つ
        self.slides.save()
        nodes = self.nodes()
        cx, cy = self.center(nodes["卒業研究"])
        rx, ry = self.center(nodes["文献調査"])
        sx, sy = self.center(nodes["発表スライド"])
        self.assertLess(ry, cy)
        self.assertGreater(sy, cy)
        self.assertAlmostEqual(math.dist((cx, cy), (rx, ry)), math.dist((cx, cy), (sx, sy)), places=3)

    def test_task_after_a_sibling_is_drawn_beyond_it(self):
        nodes = self.nodes()
        self.assertTrue(nodes["発表スライド"]["chained"])
        self.assertEqual(nodes["発表スライド"]["parent"], self.study.pk)  # データ（親子）は変えない
        top, reading, slides = (self.center(nodes[t]) for t in ["卒業研究", "文献調査", "発表スライド"])
        self.assertGreater(math.dist(top, slides), math.dist(top, reading))

    def test_top_tasks_after_each_other_are_chained_too(self):
        nxt = Task.objects.create(
            owner=self.me, title="論文を書く", category=self.research, prerequisite=self.study, on_map=True
        )
        nodes = self.nodes()
        self.assertTrue(nodes["論文を書く"]["chained"])
        self.assertEqual(nodes["論文を書く"]["prerequisite"], self.study.pk)
        self.assertEqual(nxt.pk, nodes["論文を書く"]["id"])

    def test_placed_trees_do_not_overlap(self):
        for title in ["A", "B", "C"]:
            top = Task.objects.create(owner=self.me, title=title, category=self.research, on_map=True)
            for i in range(3):
                Task.objects.create(owner=self.me, title=f"{title}{i}", parent=top)
        boxes = [(n["x"], n["y"]) for n in self.nodes().values()]
        for i, (x1, y1) in enumerate(boxes):
            for x2, y2 in boxes[i + 1 :]:
                self.assertTrue(abs(x1 - x2) >= 130 or abs(y1 - y2) >= 44, (x1, y1, x2, y2))

    def test_auto_positions_are_saved_so_nodes_do_not_jump(self):
        first = self.nodes()
        Task.objects.create(owner=self.me, title="新しいタスク", parent=self.study)
        second = self.nodes()
        for title in ["卒業研究", "文献調査", "発表スライド"]:
            self.assertEqual((first[title]["x"], first[title]["y"]), (second[title]["x"], second[title]["y"]))

    def test_arrange_resets_positions_in_the_room(self):
        self.nodes()
        self.client.post(reverse("task_map_position", args=[self.reading.pk]), {"x": "4000", "y": "4000"})
        response = self.client.post(reverse("task_map_arrange", args=[str(self.research.pk)]), follow=True)
        self.assertContains(response, "点を自動で並べ直しました．")
        self.assertLess(self.nodes()["文献調査"]["x"], 4000)

    def test_map_page_has_dialogs_and_scripts(self):
        response = self.get_room()
        for text in ['id="map-data"', 'id="map-select"', 'data-zoom="fit"', 'id="map-shelf-dialog"',
                     'id="link-dialog"', "tasks/link_dialog.js", "tasks/task_map.js", "data-add-task"]:
            self.assertContains(response, text)
        self.assertNotContains(response, "tasks/drag_link.js")
        self.assertNotContains(response, "メインタスク")

    def test_title_is_escaped_in_map_data(self):
        Task.objects.create(owner=self.me, title="<script>alert(1)</script>", parent=self.study)
        self.assertNotContains(self.get_room(), "<script>alert(1)</script>")

    def test_dock_has_map_tab(self):
        self.assertContains(self.client.get(reverse("home")), f'href="{reverse("task_map")}"')

    def test_query_count_does_not_grow_with_tasks(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.get_room()  # 1回目は場所を保存するので、2回目どうしでくらべる
        with CaptureQueriesContext(connection) as few:
            self.get_room()
        for i in range(5):
            Task.objects.create(owner=self.me, title=f"追加{i}", parent=self.study, prerequisite=self.reading)
        self.get_room()
        with CaptureQueriesContext(connection) as many:
            self.get_room()
        self.assertEqual(len(few), len(many))

    # ---- 置き場所の保存 ----
    def post_position(self, task, x, y):
        return self.client.post(reverse("task_map_position", args=[task.pk]), {"x": x, "y": y})

    def test_save_position(self):
        response = self.post_position(self.reading, "320", "45.5")
        self.assertEqual(response.json(), {"ok": True, "x": 320.0, "y": 45.5})
        self.reading.refresh_from_db()
        self.assertEqual((self.reading.map_x, self.reading.map_y), (320.0, 45.5))

    def test_position_is_kept_inside_the_map(self):
        self.post_position(self.reading, "-50", "99999")
        self.reading.refresh_from_db()
        self.assertEqual((self.reading.map_x, self.reading.map_y), (0, 5000))

    def test_bad_position_is_rejected(self):
        for x in ["abc", "nan", "inf", ""]:
            self.assertEqual(self.post_position(self.reading, x, "10").status_code, 400)

    def test_position_does_not_change_updated_at(self):
        before = self.reading.updated_at
        self.post_position(self.reading, "10", "10")
        self.reading.refresh_from_db()
        self.assertEqual(self.reading.updated_at, before)

    def test_cannot_move_others_task(self):
        self.assertEqual(self.post_position(self.others_task, "10", "10").status_code, 404)

    # ---- 名前だけで追加 ----
    def add(self, **data):
        data.setdefault("room", str(self.research.pk))
        return self.client.post(reverse("task_map_create"), data, follow=True)

    def test_add_a_top_task_to_the_room(self):
        response = self.add(title="  学会発表  ", anchor="")
        task = task_titled("学会発表")
        self.assertEqual((task.parent, task.category, task.on_map), (None, self.research, True))
        self.assertRedirects(response, self.room_url() + f"?focus={task.pk}")
        self.assertContains(response, "「学会発表」を置きました．")
        self.assertContains(self.client.get(reverse("home")), "学会発表")  # ホームのやることにも出る

    def test_add_a_top_task_to_no_category_room(self):
        self.add(title="メモ", anchor="", room="none")
        task = task_titled("メモ")
        self.assertEqual((task.category, task.on_map), (None, True))

    def test_add_inside_a_task(self):
        response = self.add(title="構成を考える", anchor=self.study.pk, relation="child")
        task = task_titled("構成を考える")
        self.assertEqual((task.parent, task.category), (self.study, self.research))
        self.assertContains(response, "「構成を考える」を「卒業研究」のサブタスクとして足しました．")

    def test_add_after_a_child_is_placed_next_to_it(self):
        self.add(title="考察を書く", anchor=self.reading.pk, relation="after")
        task = task_titled("考察を書く")
        self.assertEqual((task.parent, task.prerequisite), (self.study, self.reading))

    def test_add_after_a_top_task_makes_another_top_task_in_the_room(self):
        response = self.add(title="論文を書く", anchor=self.study.pk, relation="after")
        task = task_titled("論文を書く")
        self.assertEqual((task.parent, task.prerequisite, task.category, task.on_map), (None, self.study, self.research, True))
        self.assertIn("論文を書く", self.nodes())
        self.assertContains(response, "「論文を書く」を，「卒業研究」が終わってからやるタスクとして足しました．")

    def test_add_follows_the_depth_rule(self):
        chapter = Task.objects.create(owner=self.me, title="第1章", parent=self.reading)  # 3段目
        response = self.add(title="4段目", anchor=chapter.pk, relation="child")
        self.assertContains(response, "3段まで")
        self.assertFalse(has_task("4段目"))

    def test_add_rejects_bad_input(self):
        self.assertContains(self.add(title="   ", anchor=""), "タスクの名前を入れてください．")
        self.add(title="あ" * 201, anchor="")
        self.assertContains(self.add(title="x", anchor=self.study.pk, relation="sideways"), "つなぎ方を選んでください．")
        self.assertEqual(self.me.tasks.count(), 6)

    def test_cannot_add_to_others_task_or_room(self):
        response = self.client.post(reverse("task_map_create"), {"title": "x", "anchor": self.others_task.pk, "relation": "child"})
        self.assertEqual(response.status_code, 404)
        other_room = str(self.other.categories.first().pk)
        self.assertEqual(self.client.post(reverse("task_map_create"), {"title": "x", "room": other_room}).status_code, 404)

    # ---- つながりを外す・切り替える ----
    def post_link(self, name, task, relation):
        return self.client.post(
            reverse(name, args=[task.pk]), {"relation": relation, "next": self.room_url()}, follow=True
        )

    def test_unlink_parent_keeps_the_task_in_the_room(self):
        response = self.post_link("task_unlink", self.reading, "child")
        self.assertContains(response, "「文献調査」を「卒業研究」のサブタスクから外しました（この部屋に残ります）．")
        self.reading.refresh_from_db()
        self.assertEqual((self.reading.parent, self.reading.category, self.reading.on_map), (None, self.research, True))
        self.assertTrue(self.nodes()["文献調査"]["top"])

    def test_unlink_prerequisite(self):
        self.assertContains(self.post_link("task_unlink", self.slides, "after"), "前後のつながりを外しました．")
        self.slides.refresh_from_db()
        self.assertIsNone(self.slides.prerequisite)

    def test_switch_inside_a_top_task_to_after_keeps_it_in_the_room(self):
        response = self.post_link("task_relink", self.reading, "child")
        self.assertContains(response, "「文献調査」を，「卒業研究」が終わってからやる，に切り替えました．")
        self.reading.refresh_from_db()
        self.assertEqual((self.reading.parent, self.reading.prerequisite, self.reading.on_map), (None, self.study, True))
        self.assertTrue(self.nodes()["文献調査"]["chained"])

    def test_switch_after_to_inside(self):
        self.post_link("task_relink", self.slides, "after")
        self.slides.refresh_from_db()
        self.assertEqual((self.slides.parent, self.slides.prerequisite), (self.reading, None))

    def test_unlink_and_switch_need_something_to_change(self):
        self.assertContains(self.post_link("task_unlink", self.study, "child"), "外すつながりがありません．")
        self.assertContains(self.post_link("task_relink", self.study, "after"), "切り替えるつながりがありません．")

    def test_cannot_change_others_links(self):
        for name in ["task_unlink", "task_relink"]:
            response = self.client.post(reverse(name, args=[self.others_task.pk]), {"relation": "child"})
            self.assertEqual(response.status_code, 404)

    def test_unlink_ignores_outside_next(self):
        response = self.client.post(
            reverse("task_unlink", args=[self.reading.pk]), {"relation": "child", "next": "https://evil.example/"}
        )
        self.assertRedirects(response, reverse("task_map"), fetch_redirect_response=False)

    def test_link_from_map_returns_to_the_room(self):
        summary = Task.objects.create(owner=self.me, title="要約", parent=self.study)
        response = self.client.post(
            reverse("task_link", args=[summary.pk]),
            {"target": self.reading.pk, "relation": "child", "next": self.room_url()},
        )
        self.assertRedirects(response, self.room_url())
        summary.refresh_from_db()
        self.assertEqual(summary.parent, self.reading)


class ProgressTests(TestCase):
    """進み具合（F-06）。数えるのはサブタスクがないタスクだけ。全体・分類ごと・サブタスクのあるタスクごと。"""

    def setUp(self):
        User = get_user_model()
        self.me = User.objects.create_user("student", password="test-pass-123")
        self.other = User.objects.create_user("other", password="test-pass-123")
        cats = {c.name: c for c in self.me.categories.all()}
        self.research, self.practice = cats["研究"], cats["実習"]
        # 卒業研究（まとめ役）の中に、文献調査（済）と構成（未）→ 卒業研究そのものは数えない
        self.study = Task.objects.create(owner=self.me, title="卒業研究", category=self.research)
        Task.objects.create(owner=self.me, title="文献調査", parent=self.study, category=self.research, is_done=True)
        self.outline = Task.objects.create(owner=self.me, title="構成を考える", parent=self.study, category=self.research)
        # サブタスクがないタスク
        Task.objects.create(owner=self.me, title="実習記録", category=self.practice, is_done=True)
        self.late = Task.objects.create(
            owner=self.me, title="期限切れの課題", due_at=timezone.now() - timedelta(days=1)
        )
        Task.objects.create(owner=self.other, title="他の人のタスク", category=self.other.categories.first())
        self.client.force_login(self.me)

    def get(self):
        return self.client.get(reverse("progress"))

    def test_anonymous_user_is_sent_to_login(self):
        self.client.logout()
        self.assertRedirects(self.client.get(reverse("progress")), reverse("login") + "?next=/progress/")

    def test_overall_counts_only_tasks_without_children(self):
        overall = self.get().context["overall"]
        # 文献調査・構成・実習記録・期限切れの課題 の4件（卒業研究と、他の人のタスクは数えない）
        self.assertEqual((overall["done"], overall["total"], overall["percent"]), (2, 4, 50))
        self.assertEqual(overall["overdue"], 1)

    def test_week_done_counts_last_seven_days(self):
        old = Task.objects.create(owner=self.me, title="前に終えた", is_done=True)
        Task.objects.filter(pk=old.pk).update(done_at=timezone.now() - timedelta(days=8))
        self.assertEqual(self.get().context["overall"]["week_done"], 2)  # 8日前のものは数えない

    def test_categories_in_created_order_with_no_category_last(self):
        categories = self.get().context["categories"]
        self.assertEqual([c["name"] for c in categories], ["実習", "研究", "分類なし"])
        research = categories[1]
        self.assertEqual((research["done"], research["total"]), (1, 2))
        self.assertEqual([t.title for t in research["open"]], ["構成を考える"])
        self.assertEqual(categories[2]["overdue"], 1)

    def test_main_tasks_show_only_open_ones_with_children(self):
        main_tasks = self.get().context["main_tasks"]
        self.assertEqual([m["task"].title for m in main_tasks], ["卒業研究"])  # サブタスクがないタスクは出さない
        self.assertEqual((main_tasks[0]["done"], main_tasks[0]["total"]), (1, 2))
        self.study.is_done = True
        self.study.save()
        self.assertEqual(self.get().context["main_tasks"], [])  # 終わったメインタスクは出さない

    def test_grandchildren_count_toward_their_main_task(self):
        Task.objects.create(owner=self.me, title="第1章", parent=self.outline)
        main = self.get().context["main_tasks"][0]
        # 構成を考える はサブタスクができたので数えず、第1章を数える
        self.assertEqual((main["done"], main["total"]), (1, 2))

    def test_page_shows_bars_and_links(self):
        response = self.get()
        self.assertContains(response, "1件 ＝ サブタスクのないタスク")
        self.assertContains(response, reverse("task_detail", args=[self.outline.pk]))
        self.assertContains(response, reverse("task_map_root", args=[self.study.pk]))
        self.assertContains(response, "<progress", count=1 + 3 + 1)  # 全体・分類3つ・メインタスク1つ
        self.assertNotContains(response, "他の人のタスク")

    def test_empty_state(self):
        self.me.tasks.all().delete()
        self.assertContains(self.get(), "まだタスクがありません")

    def test_dock_links_to_progress(self):
        response = self.get()
        self.assertContains(response, f'href="{reverse("progress")}" class="dock-active"')
        self.assertNotContains(self.client.get(reverse("home")), "準備中")

    def test_query_count_does_not_grow_with_tasks(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as few:
            self.get()
        for i in range(5):
            Task.objects.create(owner=self.me, title=f"追加{i}", parent=self.study, prerequisite=self.outline)
        with CaptureQueriesContext(connection) as many:
            self.get()
        self.assertEqual(len(few), len(many))


class CategoryFollowsTopTests(TestCase):
    """分類は、いちばん上のタスクにそろう（Issue #34）。"""

    def setUp(self):
        User = get_user_model()
        self.me = User.objects.create_user("student", password="test-pass-123")
        cats = {c.name: c for c in self.me.categories.all()}
        self.lesson, self.research = cats["授業"], cats["研究"]
        self.study = Task.objects.create(owner=self.me, title="卒業研究", category=self.research)
        self.report = Task.objects.create(owner=self.me, title="レポート", category=self.lesson)
        self.client.force_login(self.me)

    def refresh(self, *tasks):
        for task in tasks:
            task.refresh_from_db()

    # ---- モデル ----
    def test_child_and_grandchild_take_the_top_category(self):
        child = Task.objects.create(owner=self.me, title="文献調査", parent=self.study, category=self.lesson)
        grandchild = Task.objects.create(owner=self.me, title="第1章", parent=child)
        self.assertEqual(child.category, self.research)  # 自分で「授業」を選んでも、上にそろう
        self.assertEqual(grandchild.category, self.research)

    def test_changing_the_top_category_updates_its_tasks(self):
        child = Task.objects.create(owner=self.me, title="文献調査", parent=self.study)
        grandchild = Task.objects.create(owner=self.me, title="第1章", parent=child)
        self.study.category = self.lesson
        self.study.save()
        self.refresh(child, grandchild)
        self.assertEqual((child.category, grandchild.category), (self.lesson, self.lesson))

    def test_moving_a_task_brings_its_children_to_the_new_category(self):
        sub = Task.objects.create(owner=self.me, title="問1", parent=self.report)
        self.report.parent = self.study
        self.report.save()
        self.refresh(self.report, sub)
        self.assertEqual((self.report.category, sub.category), (self.research, self.research))

    def test_taking_a_task_out_keeps_its_category(self):
        child = Task.objects.create(owner=self.me, title="文献調査", parent=self.study)
        child.parent = None
        child.save()
        self.refresh(child)
        self.assertEqual(child.category, self.research)

    def test_save_with_update_fields_also_saves_category(self):
        # ドラッグでつなぐとき（task_link）は update_fields で保存する。それでも分類がそろう
        self.report.parent = self.study
        self.report.save(update_fields=["parent", "updated_at"])
        self.refresh(self.report)
        self.assertEqual(self.report.category, self.research)

    def test_migration_fixes_old_mismatched_data(self):
        import importlib

        from django.apps import apps

        child = Task.objects.create(owner=self.me, title="文献調査", parent=self.study)
        Task.objects.filter(pk=child.pk).update(category=self.lesson)  # 前のちぐはぐなデータを再現（save を通さない）
        migration = importlib.import_module("tasks.migrations.0010_sync_child_categories")
        migration.sync_child_categories(apps, None)
        self.refresh(child)
        self.assertEqual(child.category, self.research)

    # ---- 画面 ----
    def test_form_ignores_category_when_parent_is_chosen(self):
        response = self.client.post(
            reverse("task_create"),
            {"title": "問1", "parent": self.study.pk, "category": "__new__", "new_category_name": "", "importance": 2},
        )
        self.assertEqual(response.status_code, 302)  # 新しい分類の名前がなくても、親があれば止めない
        task = task_titled("問1")
        self.assertEqual(task.category, self.research)
        self.assertEqual(self.me.categories.count(), 4)  # 分類は増えない

    def test_form_shows_parent_categories(self):
        response = self.client.get(reverse("task_create"))
        self.assertContains(response, 'id="parent-categories"')
        self.assertContains(response, 'id="category-follows-parent"')
        names = response.context["form"].parent_category_names
        self.assertEqual(names[str(self.study.pk)], "研究")

    def test_link_dialog_tells_category_change(self):
        url = reverse("task_link_options", args=[self.report.pk])
        data = self.client.get(url, {"target": self.study.pk}).json()
        self.assertEqual(data["child"]["notes"], ["分類：授業 → 研究（サブタスクもそろう）"])
        self.assertEqual(data["after"]["notes"], [])  # 前後でつないでも、分類は変わらない

    def test_link_dialog_tells_replacing_current_links(self):
        other = Task.objects.create(owner=self.me, title="ゼミ", category=self.research)
        self.report.parent = self.study
        self.report.prerequisite = other
        self.report.save()
        url = reverse("task_link_options", args=[self.report.pk])
        # 今と同じ相手（ゼミ）とは、前後は入れかえにならない
        self.assertEqual(self.client.get(url, {"target": other.pk}).json()["after"]["notes"], [])
        # 別の相手（発表）なら、今の親・今の先にやることを入れかえることになる
        target = Task.objects.create(owner=self.me, title="発表", category=self.research)
        data = self.client.get(url, {"target": target.pk}).json()
        self.assertIn("⚠ 今：「卒業研究」のサブタスク → 入れかえ", data["child"]["notes"])
        self.assertIn("⚠ 今：「ゼミ」が終わってから → 入れかえ", data["after"]["notes"])

    def test_drag_into_another_category_moves_it(self):
        self.client.post(reverse("task_link", args=[self.report.pk]), {"target": self.study.pk, "relation": "child"})
        self.refresh(self.report)
        self.assertEqual(self.report.category, self.research)


class TaskModelTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user("student", password="test-pass-123")
        self.other = User.objects.create_user("other", password="test-pass-123")

    def make(self, title, parent=None, owner=None):
        return Task.objects.create(owner=owner or self.user, title=title, parent=parent)

    def test_depth_counts_from_top(self):
        exam = self.make("国家試験")
        subject = self.make("薬理学", parent=exam)
        chapter = self.make("問題集 第1章", parent=subject)
        self.assertEqual(exam.depth(), 1)
        self.assertEqual(subject.depth(), 2)
        self.assertEqual(chapter.depth(), 3)

    def test_fourth_level_is_rejected(self):
        exam = self.make("国家試験")
        subject = self.make("薬理学", parent=exam)
        chapter = self.make("問題集 第1章", parent=subject)
        too_deep = Task(owner=self.user, title="1ページ目", parent=chapter)
        with self.assertRaises(ValidationError):
            too_deep.full_clean()

    def test_moving_a_subtree_too_deep_is_rejected(self):
        # 2段の木を、2段目の下に動かすと4段になる
        a = self.make("A")
        b = self.make("B", parent=a)
        c = self.make("C")
        self.make("D", parent=c)
        c.parent = b
        with self.assertRaises(ValidationError):
            c.full_clean()

    def test_cannot_be_own_ancestor(self):
        parent = self.make("親")
        child = self.make("子", parent=parent)
        parent.parent = child
        with self.assertRaises(ValidationError):
            parent.full_clean()

    def test_parent_must_belong_to_same_owner(self):
        others_task = self.make("他の人のタスク", owner=self.other)
        mine = Task(owner=self.user, title="自分のタスク", parent=others_task)
        with self.assertRaises(ValidationError):
            mine.full_clean()

    def test_done_at_is_set_and_cleared(self):
        task = self.make("日誌を書く")
        self.assertIsNone(task.done_at)
        task.is_done = True
        task.save()
        self.assertIsNotNone(task.done_at)
        task.is_done = False
        task.save()
        self.assertIsNone(task.done_at)

    def test_progress_of_children(self):
        parent = self.make("卒論")
        self.assertIsNone(parent.progress())
        Task.objects.create(owner=self.user, title="文献調査", parent=parent, is_done=True)
        self.make("構成を考える", parent=parent)
        self.assertEqual(parent.progress(), 50)

    def test_ordering_puts_important_and_near_deadline_first(self):
        low = Task.objects.create(owner=self.user, title="低", importance=Task.Importance.LOW)
        high = Task.objects.create(owner=self.user, title="高", importance=Task.Importance.HIGH)
        done = Task.objects.create(
            owner=self.user, title="済", importance=Task.Importance.HIGH, is_done=True
        )
        self.assertEqual(list(Task.objects.all()), [high, low, done])

    def test_due_status(self):
        # 基準の時刻を決めて、締切との差で状態が変わることを確かめる
        now = timezone.make_aware(datetime(2026, 11, 20, 12, 0))
        task = self.make("課題")
        self.assertIsNone(task.due_status(now))
        task.due_at = now - timedelta(hours=1)
        self.assertEqual(task.due_status(now), "overdue")
        task.is_done = True  # 完了していれば期限切れにはしない
        self.assertEqual(task.due_status(now), "today")
        task.is_done = False
        task.due_at = now + timedelta(hours=6)
        self.assertEqual(task.due_status(now), "today")
        task.due_at = now + timedelta(days=2)
        self.assertEqual(task.due_status(now), "soon")
        task.due_at = now + timedelta(days=10)
        self.assertEqual(task.due_status(now), "later")


class BackNavigationTests(TestCase):
    """来た画面に戻る（Issue #41）。?back= で運んだ戻り先を、詳細・編集・サブタスク・資料・分類のあとも保つ。"""

    def setUp(self):
        self.me = get_user_model().objects.create_user("student", password="test-pass-123")
        self.research = named(self.me, "研究")
        self.study = Task.objects.create(owner=self.me, title="卒業研究", category=self.research, on_map=True)
        self.reading = Task.objects.create(owner=self.me, title="文献調査", parent=self.study)
        self.client.force_login(self.me)
        self.room = reverse("task_map_room", args=[self.research.pk])
        self.detail = reverse("task_detail", args=[self.study.pk])

    def back(self, path=None):
        return "?" + urlencode({"back": path or self.room})

    def test_map_nodes_link_to_detail_with_back(self):
        nodes = self.client.get(self.room).context["nodes"]
        self.assertEqual(nodes[0]["url"], self.detail + self.back())

    def test_detail_from_map_goes_back_to_map_and_highlights_the_task(self):
        response = self.client.get(self.detail + self.back())
        self.assertEqual(response.context["back"], {"url": f"{self.room}?focus={self.study.pk}", "label": "🗺️ マップ"})
        self.assertContains(response, "← 🗺️ マップ")
        self.assertNotContains(response, "← ホーム")
        # 編集・＋ サブタスク・サブタスクのカードにも、戻り先を付けたままにする
        self.assertContains(response, reverse("task_update", args=[self.study.pk]) + self.back())
        self.assertContains(response, reverse("task_create_child", args=[self.study.pk]) + self.back())
        self.assertContains(response, reverse("task_detail", args=[self.reading.pk]) + self.back())

    def test_detail_without_back_works_as_before(self):
        self.assertContains(self.client.get(self.detail), "← ホーム")
        self.assertContains(self.client.get(reverse("task_detail", args=[self.reading.pk])), "← 卒業研究")

    def test_unsafe_or_unknown_back_is_ignored(self):
        for bad in ["https://evil.example.com/", "//evil.example.com/map/room/1/", "/admin/", "/tasks/1/", "map/room/1/", "/nothing/"]:
            response = self.client.get(self.detail + "?" + urlencode({"back": bad}))
            self.assertIsNone(response.context["back"], bad)
            self.assertEqual(response.context["back_query"], "", bad)
            self.assertContains(response, "← ホーム")

    def test_back_keeps_only_the_path(self):
        response = self.client.get(self.detail + self.back(self.room + "?focus=999&x=1"))
        self.assertEqual(response.context["back_path"], self.room)

    def test_edit_keeps_back_after_saving_and_cancel(self):
        url = reverse("task_update", args=[self.study.pk]) + self.back()
        response = self.client.get(url)
        self.assertEqual(response.context["cancel_url"], self.detail + self.back())
        self.assertContains(response, f'name="back" value="{self.room}"')
        response = self.client.post(url, {"title": "卒業研究（本番）", "importance": 2, "category": self.research.pk})
        self.assertRedirects(response, self.detail + self.back())

    def test_edit_form_back_field_also_works(self):
        # アドレスに back がなくても、フォームの中の back（hidden）で戻り先がわかる
        response = self.client.post(
            reverse("task_update", args=[self.study.pk]),
            {"title": "卒業研究", "importance": 2, "category": self.research.pk, "back": self.room},
        )
        self.assertRedirects(response, self.detail + self.back())

    def test_adding_a_subtask_returns_with_back(self):
        url = reverse("task_create_child", args=[self.study.pk]) + self.back()
        self.assertEqual(self.client.get(url).context["cancel_url"], self.detail + self.back())
        response = self.client.post(url, {"title": "構成を考える", "parent": self.study.pk, "importance": 2})
        self.assertRedirects(response, self.detail + self.back())

    def test_sources_return_with_back(self):
        response = self.client.post(
            reverse("source_create", args=[self.study.pk]), {"kind": "paper", "memo": "プリント", "back": self.room}
        )
        self.assertRedirects(response, self.detail + self.back())
        source = self.study.sources.get()
        response = self.client.post(reverse("source_delete", args=[source.pk]), {"back": self.room})
        self.assertRedirects(response, self.detail + self.back())

    def test_toggle_on_detail_returns_to_the_same_detail(self):
        response = self.client.post(
            reverse("task_toggle", args=[self.reading.pk]), {"next": self.detail + self.back()}
        )
        self.assertRedirects(response, self.detail + self.back())

    def test_progress_links_carry_back(self):
        response = self.client.get(reverse("progress"))
        self.assertContains(response, self.detail + "?back=%2Fprogress%2F")
        detail = self.client.get(self.detail + "?back=%2Fprogress%2F")
        self.assertEqual(detail.context["back"], {"url": "/progress/", "label": "📊 進み具合"})

    def test_category_page_from_map_goes_back_to_map(self):
        self.assertContains(self.client.get(self.room), reverse("category_list") + "?back=" + self.room.replace("/", "%2F"))
        url = reverse("category_list") + self.back()
        self.assertContains(self.client.get(url), "← 🗺️ マップ")
        response = self.client.post(reverse("category_create"), {"name": "部活", "back": self.room})
        self.assertRedirects(response, url)
        self.assertContains(self.client.get(reverse("category_list")), "← ホーム")


class RelatedLinkTests(TestCase):
    """🔗 関連（Issue #43）。順番なし・向きなし・いくつでも。マップと詳細で付ける・見る・外す。"""

    def setUp(self):
        User = get_user_model()
        self.me = User.objects.create_user("student", password="test-pass-123")
        self.other = User.objects.create_user("other", password="test-pass-123")
        cats = {c.name: c for c in self.me.categories.all()}
        self.research, self.lesson = cats["研究"], cats["授業"]
        self.study = Task.objects.create(owner=self.me, title="卒業研究", category=self.research, on_map=True)
        self.reading = Task.objects.create(owner=self.me, title="文献調査", parent=self.study)
        self.slides = Task.objects.create(owner=self.me, title="発表スライド", parent=self.study, prerequisite=self.reading)
        self.paper = Task.objects.create(owner=self.me, title="論文を読む", category=self.research, on_map=True)
        self.report = Task.objects.create(owner=self.me, title="実習レポート", category=self.lesson, on_map=True)
        self.others_task = Task.objects.create(owner=self.other, title="他の人のタスク")
        self.client.force_login(self.me)

    def options(self, source, target):
        url = reverse("task_link_options", args=[source.pk])
        return self.client.get(url, {"target": target.pk}).json()

    def link(self, source, target, relation="related"):
        data = {"target": target.pk, "relation": relation}
        return self.client.post(reverse("task_link", args=[source.pk]), data, follow=True)

    def unlink(self, task, target, **extra):
        data = {"relation": "related", "target": target.pk, **extra}
        return self.client.post(reverse("task_unlink", args=[task.pk]), data, follow=True)

    def map_nodes(self, category):
        response = self.client.get(reverse("task_map_room", args=[category.pk]))
        return {node["title"]: node for node in response.context["nodes"]}

    # ---- つなげるか ----
    def test_options_allow_related_for_unconnected_tasks(self):
        data = self.options(self.reading, self.report)
        self.assertEqual(data["related"], {"ok": True, "reason": "", "notes": []})

    def test_options_explain_why_related_is_not_allowed(self):
        self.assertEqual(self.options(self.reading, self.reading)["related"]["reason"], "同じタスクです．")
        # 📁（親子）・🔒（前後）で直接つながっている相手は、どちらから見てもだめ
        self.assertEqual(self.options(self.reading, self.study)["related"]["reason"], "📁 でつながっています．")
        self.assertEqual(self.options(self.study, self.reading)["related"]["reason"], "📁 でつながっています．")
        self.assertEqual(self.options(self.slides, self.reading)["related"]["reason"], "🔒 でつながっています．")
        self.assertEqual(self.options(self.reading, self.slides)["related"]["reason"], "🔒 でつながっています．")
        self.paper.related.add(self.report)
        self.assertEqual(self.options(self.report, self.paper)["related"]["reason"], "もう 🔗 でつながっています．")

    def test_cannot_relate_to_another_users_task(self):
        self.assertEqual(self.client.get(reverse("task_link_options", args=[self.paper.pk]), {"target": self.others_task.pk}).status_code, 404)
        self.assertEqual(self.link(self.paper, self.others_task).status_code, 404)
        self.assertEqual(self.paper.related_problem(self.others_task), "他の人のタスクとはつなげません．")
        self.assertFalse(self.paper.related.exists())

    # ---- つなぐ ----
    def test_link_related_works_both_ways_and_many_times(self):
        response = self.link(self.reading, self.report)
        self.assertContains(response, "「文献調査」と「実習レポート」を 🔗 でつなぎました")
        self.link(self.reading, self.paper)
        # 向きがないので、相手から見ても つながっている
        self.assertEqual(set(self.reading.related.all()), {self.report, self.paper})
        self.assertEqual(list(self.report.related.all()), [self.reading])
        # 親子・前後・分類は変わらない
        self.reading.refresh_from_db()
        self.assertEqual(self.reading.parent, self.study)
        self.assertEqual(self.reading.category, self.research)

    def test_link_related_twice_is_refused(self):
        self.link(self.paper, self.report)
        response = self.link(self.report, self.paper)
        self.assertContains(response, "もう 🔗 でつながっています．")
        self.assertEqual(self.paper.related.count(), 1)

    def test_making_folder_or_lock_link_replaces_related(self):
        self.paper.related.add(self.report, self.study)
        # つなぐ前に「🔗 は外れる」と知らせる
        self.assertIn("🔗 → 外れる（線は1本だけ）", self.options(self.paper, self.study)["child"]["notes"])
        self.link(self.paper, self.study, "child")
        self.link(self.report, self.paper, "after")
        self.assertFalse(self.paper.related.exists())

    # ---- 外す ----
    def test_unlink_related_from_either_side(self):
        self.paper.related.add(self.report)
        response = self.unlink(self.report, self.paper, next=reverse("task_detail", args=[self.report.pk]))
        self.assertRedirects(response, reverse("task_detail", args=[self.report.pk]))
        self.assertContains(response, "「実習レポート」と「論文を読む」の 🔗 を外しました")
        self.assertFalse(self.paper.related.exists())
        # タスクは消えない
        self.assertTrue(Task.objects.filter(pk=self.report.pk).exists())

    def test_unlink_related_needs_a_connected_target(self):
        self.assertContains(self.unlink(self.paper, self.report), "外すつながりがありません．")
        response = self.client.post(reverse("task_unlink", args=[self.paper.pk]), {"relation": "related", "target": "x"}, follow=True)
        self.assertContains(response, "外すつながりがありません．")
        # 他の人のタスクのつながりは外せない
        self.others_task.related.add(Task.objects.create(owner=self.other, title="他の人の2"))
        self.assertEqual(self.unlink(self.others_task, self.others_task.related.get()).status_code, 404)

    def test_deleting_a_task_removes_its_related_links(self):
        self.paper.related.add(self.report)
        self.report.delete()
        self.assertFalse(self.paper.related.exists())

    # ---- 見せる ----
    def test_map_sends_related_partners_in_the_same_room_only(self):
        self.paper.related.add(self.slides, self.report)  # 実習レポートは「授業」の部屋
        nodes = self.map_nodes(self.research)
        self.assertEqual(nodes["論文を読む"]["related"], [self.slides.pk])
        self.assertEqual(nodes["発表スライド"]["related"], [self.paper.pk])
        self.assertEqual(nodes["卒業研究"]["related"], [])
        self.assertEqual(self.map_nodes(self.lesson)["実習レポート"]["related"], [])

    def test_detail_lists_related_tasks_from_every_room(self):
        detail = reverse("task_detail", args=[self.paper.pk])
        self.assertContains(self.client.get(detail), "🔗 なし")
        self.paper.related.add(self.report)
        response = self.client.get(detail + "?" + urlencode({"back": reverse("task_map_room", args=[self.research.pk])}))
        self.assertNotContains(response, "🔗 なし")
        # 相手へのリンクにも戻り先を付け、✕ で外したら同じ画面（戻り先つき）に戻る
        self.assertContains(response, reverse("task_detail", args=[self.report.pk]) + response.context["back_query"])
        self.assertContains(response, '<input type="hidden" name="target" value="%d">' % self.report.pk)
        self.assertContains(response, "「実習レポート」との 🔗 を外す")

    def test_link_dialog_offers_related(self):
        self.assertContains(self.client.get(reverse("home")), 'value="related"')


class GuideTests(TestCase):
    """使い方とヘルプ（Issue #47）。"""

    def setUp(self):
        self.user = get_user_model().objects.create_user("student", password="test-pass-123")

    def test_guide_and_help_need_login(self):
        for name in ("guide", "help"):
            with self.subTest(name=name):
                url = reverse(name)
                self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")
        # ログインしていない画面には ？ を出さない
        self.assertNotContains(self.client.get(reverse("login")), f'href="{reverse("help")}')

    def test_guide_has_cards_for_every_chapter(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("guide"))
        self.assertTemplateUsed(response, "tasks/guide.html")
        self.assertContains(response, "tasks/guide.js")
        html = response.content.decode()
        slides = html.split('<section class="guide-slide"')[1:]
        self.assertGreaterEqual(len(slides), 15)
        keys = set()
        for slide in slides:
            # どのカードにも、章・絵（読み上げない）・見出しがある
            match = re.match(r' data-chapter="[^"]+" data-key="([a-z]+)"', slide)
            self.assertIsNotNone(match, slide[:80])
            keys.add(match.group(1))
            self.assertIn('class="guide-art" aria-hidden="true"', slide)
            self.assertIn('class="guide-title"', slide)
        # ヘルプの章のリンク（/guide/#map など）は、どれも本当にある章を指す
        self.assertEqual(keys, {key for key, _, _ in GUIDE_CHAPTERS})

    def test_help_links_to_each_chapter_and_lists_symbols(self):
        self.client.force_login(self.user)
        # 上のバーの ？ からヘルプへ
        self.assertContains(self.client.get(reverse("home")), f'href="{reverse("help")}?from=')
        response = self.client.get(reverse("help"))
        self.assertContains(response, f'href="{reverse("guide")}"')
        self.assertContains(response, f'href="{reverse("guide")}#map"')
        for symbol in ("📁", "🔒", "🔗", "📦", "⚠"):
            self.assertContains(response, symbol)


class FeedbackTests(TestCase):
    """ヘルプの「✉️ 意見を送る」（Issue #48）。"""

    def setUp(self):
        User = get_user_model()
        self.me = User.objects.create_user("student", password="test-pass-123")
        self.client.force_login(self.me)
        self.url = reverse("feedback_create")

    def send(self, **extra):
        data = {"kind": "bug", "body": "線を引くと点がずれる", **extra}
        return self.client.post(self.url, data, follow=True)

    def test_needs_login(self):
        self.client.logout()
        self.assertRedirects(self.client.get(self.url), f"{reverse('login')}?next={self.url}")

    def test_help_links_to_feedback_with_the_page(self):
        # ？ は押した画面を from で運ぶ → ヘルプ → 意見を送る にも運ぶ
        home = self.client.get(reverse("home"))
        self.assertContains(home, reverse("help") + "?from=%2F")
        response = self.client.get(reverse("help"), {"from": "/map/room/3/"})
        self.assertContains(response, self.url + "?from=%2Fmap%2Froom%2F3%2F")

    def test_send_feedback(self):
        response = self.send(**{"from": "/map/room/3/?focus=5"})
        self.assertRedirects(response, reverse("help"))
        self.assertContains(response, "✉️ 送りました．ありがとうございます！")
        feedback = Feedback.objects.get()
        self.assertEqual(feedback.user, self.me)
        self.assertEqual(feedback.kind, Feedback.Kind.BUG)
        self.assertEqual(feedback.body, "線を引くと点がずれる")
        self.assertEqual(feedback.page, "/map/room/3/")  # ? から後ろは残さない
        self.assertFalse(feedback.is_resolved)

    def test_page_outside_the_site_is_not_kept(self):
        for value in ("https://example.com/x", "//example.com/x", "map"):
            with self.subTest(value=value):
                self.send(**{"from": value})
                self.assertEqual(Feedback.objects.latest("pk").page, "")

    def test_kind_and_body_are_required(self):
        response = self.client.post(self.url, {"kind": "", "body": ""})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].errors.keys(), {"kind", "body"})
        response = self.client.post(self.url, {"kind": "good", "body": "あ" * 1001})
        self.assertIn("body", response.context["form"].errors)
        self.assertFalse(Feedback.objects.exists())

    def test_daily_limit(self):
        # 今日の分は上限より1件少なく、前の日の分は数えない
        for _ in range(Feedback.DAILY_LIMIT - 1):
            Feedback.objects.create(user=self.me, kind="idea", body="もっと")
        old = Feedback.objects.create(user=self.me, kind="idea", body="前の日")
        Feedback.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=2))
        self.send()  # これで今日の上限ちょうど
        self.assertEqual(Feedback.objects.filter(body="線を引くと点がずれる").count(), 1)
        response = self.send(body="もう1件")
        self.assertContains(response, "今日はここまで")
        self.assertFalse(Feedback.objects.filter(body="もう1件").exists())

    def test_feedback_stays_when_the_user_is_deleted(self):
        self.send()
        self.me.delete()
        self.assertIsNone(Feedback.objects.get().user)

    def test_admin_lists_feedback_and_only_resolved_can_change(self):
        self.send()
        admin_user = get_user_model().objects.create_superuser("kanri", password="test-pass-123")
        self.client.force_login(admin_user)
        changelist = reverse("admin:tasks_feedback_changelist")
        self.assertContains(self.client.get(changelist), "線を引くと点がずれる")
        feedback = Feedback.objects.get()
        # 届いた内容は書きかえられない（読むだけ）
        response = self.client.get(reverse("admin:tasks_feedback_change", args=[feedback.pk]))
        self.assertNotContains(response, 'name="body"')
        self.assertContains(response, 'name="is_resolved"')
        self.assertEqual(self.client.get(reverse("admin:tasks_feedback_add")).status_code, 403)


ENCRYPTION_MEDIA = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=ENCRYPTION_MEDIA)
class EncryptionTests(TestCase):
    """タスクの中身を暗号化して保存する・管理画面には件数だけ（Issue #49）。"""

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(ENCRYPTION_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.me = get_user_model().objects.create_user("student", password="test-pass-123")
        self.research = named(self.me, "研究")
        self.study = Task.objects.create(owner=self.me, title="卒業研究", memo="山田先生に相談", category=self.research, on_map=True)
        self.reading = Task.objects.create(owner=self.me, title="文献調査", parent=self.study)
        self.source = Source.objects.create(task=self.study, kind="classroom", url="https://example.com/kadai", memo="考察を厚く")
        self.client.force_login(self.me)

    def raw(self, sql):
        """データベースに入っている文字を、そのまま（元に戻さずに）読む。ファイルを直接開いたときと同じ見え方。"""
        with connection.cursor() as cursor:
            cursor.execute(sql)
            return [value for row in cursor.fetchall() for value in row]

    def test_database_holds_no_readable_text(self):
        stored = (
            self.raw("SELECT title, memo FROM tasks_task")
            + self.raw("SELECT url, memo FROM tasks_source")
            + self.raw("SELECT name FROM tasks_category")
        )
        for plain in ("卒業研究", "山田先生に相談", "文献調査", "example.com", "考察を厚く", "研究"):
            self.assertFalse(any(plain in value for value in stored if value), plain)
        self.assertTrue(all(is_encrypted_text(value) for value in stored if value))
        # 空のメモは空のまま（中身はないので、隠すものもない）
        self.assertIn("", self.raw("SELECT memo FROM tasks_task"))

    def test_app_reads_the_original_text(self):
        task = Task.objects.get(pk=self.study.pk)
        self.assertEqual((task.title, task.memo, task.category.name), ("卒業研究", "山田先生に相談", "研究"))
        self.assertEqual(Source.objects.get().url, "https://example.com/kadai")
        self.assertContains(self.client.get(reverse("task_detail", args=[self.study.pk])), "山田先生に相談")

    def test_same_text_is_stored_differently_each_time(self):
        a = Task.objects.create(owner=self.me, title="同じ")
        b = Task.objects.create(owner=self.me, title="同じ")
        first, second = self.raw(f"SELECT title FROM tasks_task WHERE id IN ({a.pk}, {b.pk})")
        self.assertNotEqual(first, second)  # 同じ文字でも、保存された形はちがう（同じかどうかも分からない）

    def test_searching_inside_encrypted_text_is_refused(self):
        # 暗号化した欄で探すと、黙って0件にならず、すぐエラーになる
        with self.assertRaises(FieldError):
            Task.objects.filter(title="卒業研究").count()
        with self.assertRaises(FieldError):
            self.me.categories.filter(name__contains="研").count()
        self.assertEqual(Task.objects.filter(memo__isnull=False).count(), 2)

    def test_old_key_still_reads_after_adding_a_new_key(self):
        from django.conf import settings
        from cryptography.fernet import Fernet

        new_key = Fernet.generate_key().decode()
        with override_settings(FIELD_ENCRYPTION_KEYS=[new_key, *settings.FIELD_ENCRYPTION_KEYS]):
            self.assertEqual(Task.objects.get(pk=self.study.pk).title, "卒業研究")

    def test_photo_file_is_encrypted_on_disk(self):
        url = reverse("source_create", args=[self.study.pk])
        self.client.post(url, {"kind": "paper", "url": "", "memo": "", "photo": make_image()})
        source = self.study.sources.exclude(photo="").get()
        with open(source.photo.path, "rb") as file:
            stored = file.read()
        self.assertFalse(stored.startswith(b"\xff\xd8"))  # JPEG のはじまりの印がない＝そのままでは開けない
        self.assertTrue(decrypt_bytes(stored).startswith(b"\xff\xd8"))  # 鍵で元に戻すと JPEG
        # 持ち主が見る画面では、元の写真に戻して返す
        response = self.client.get(reverse("source_photo", args=[source.pk]))
        self.assertTrue(b"".join(response.streaming_content).startswith(b"\xff\xd8"))

    def test_category_names_stay_unique_without_the_database_rule(self):
        self.client.post(reverse("category_create"), {"name": "研究"})
        self.assertEqual(sum(c.name == "研究" for c in self.me.categories.all()), 1)
        lesson = named(self.me, "授業")
        response = self.client.post(reverse("category_update", args=[lesson.pk]), {"name": "研究"}, follow=True)
        self.assertContains(response, "分類「研究」はもうあります．")
        lesson.refresh_from_db()
        self.assertEqual(lesson.name, "授業")

    def test_admin_shows_counts_but_no_contents(self):
        admin_user = get_user_model().objects.create_superuser("kanri", password="test-pass-123")
        self.client.force_login(admin_user)
        # タスク・分類・資料の画面はない
        for name in ("task", "category", "source"):
            with self.subTest(model=name):
                with self.assertRaises(NoReverseMatch):
                    reverse(f"admin:tasks_{name}_changelist")
        response = self.client.get(reverse("admin:tasks_usage_changelist"))
        self.assertEqual(response.status_code, 200)
        for plain in ("卒業研究", "山田先生に相談", "文献調査", "考察を厚く"):
            self.assertNotContains(response, plain)
        student = next(row for row in response.context["cl"].result_list if row.username == "student")
        self.assertEqual((student.task_total, student.subtask_total, student.on_map_total, student.source_total), (2, 1, 1, 1))
        self.assertIn(("📁 サブタスク", 1), response.context["usage_summary"])
        # 見るだけ：1人ずつの画面・足す・消すはない
        self.assertEqual(self.client.get(reverse("admin:tasks_usage_change", args=[self.me.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:tasks_usage_add")).status_code, 403)

    def test_help_explains_privacy(self):
        response = self.client.get(reverse("help"))
        self.assertContains(response, "🛡️ プライバシー")
        self.assertContains(response, "管理画面に出るのは件数だけ")


class StaticCssTests(TestCase):
    """前もって作った CSS（Issue #55）。CDN の Tailwind（ブラウザ版）や daisyUI の全部は読みこまない。"""

    def test_pages_load_the_built_css_only(self):
        user = get_user_model().objects.create_user("student", password="test-pass-123")
        self.client.force_login(user)
        for url in (reverse("home"), reverse("login")):
            with self.subTest(url=url):
                self.client.force_login(user) if url != reverse("login") else self.client.logout()
                response = self.client.get(url)
                self.assertContains(response, 'href="/static/css/app.css"')
                self.assertNotContains(response, "cdn.jsdelivr.net")
                self.assertNotContains(response, "tailwindcss/browser")

    def test_built_css_exists_and_has_daisyui(self):
        css = (Path(__file__).resolve().parent.parent / "static" / "css" / "app.css").read_text(encoding="utf-8")
        self.assertIn("--color-base-100", css)  # daisyUI のテーマの色
        self.assertIn(".btn", css)
        self.assertLess(len(css), 300_000)  # 全部の部品（約1.1MB）ではなく、使っている分だけ



class HtmxTests(TestCase):
    """押したときに、ページを読み直さず画面の一部だけ書きかえる（htmx・Issue #57）。"""

    HX = {"HTTP_HX_REQUEST": "true"}

    def setUp(self):
        User = get_user_model()
        self.me = User.objects.create_user("student", password="test-pass-123")
        self.other = User.objects.create_user("other", password="test-pass-123")
        self.study = Task.objects.create(owner=self.me, title="卒業研究")
        self.reading = Task.objects.create(owner=self.me, title="文献調査", parent=self.study)
        self.slides = Task.objects.create(owner=self.me, title="発表スライド", parent=self.study, prerequisite=self.reading)
        self.client.force_login(self.me)

    def toggle(self, task, on_page):
        return self.client.post(reverse("task_toggle", args=[task.pk]), {"on_page": ",".join(on_page)}, **self.HX)

    def test_base_loads_htmx_and_sends_csrf_token(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, 'src="/static/js/htmx.min.js"')
        self.assertContains(response, 'src="/static/js/app.js"')
        self.assertContains(response, 'hx-headers=\'{"X-CSRFToken": "')

    def test_toggle_card_returns_only_the_card_and_counts(self):
        response = self.toggle(self.study, [f"card-{self.study.pk}", "open-count", "done-count"])
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn("<html", html)  # ページ全体ではない
        self.assertIn(f'id="card-{self.study.pk}"', html)
        self.assertIn("を未完了に戻す", html)  # ✓ になった
        self.assertIn('<span id="open-count" hx-swap-oob="innerHTML">0</span>', html)
        self.assertIn('<span id="done-count" hx-swap-oob="innerHTML">1</span>', html)
        self.study.refresh_from_db()
        self.assertTrue(self.study.is_done)
        # お知らせは出さない（次に開いた画面に、古いお知らせが出ないように）
        self.assertNotContains(self.client.get(reverse("home")), "完了にしました")

    def test_toggle_row_also_updates_parent_progress_and_unlocks_next(self):
        on_page = [f"row-{self.reading.pk}", f"row-{self.slides.pk}", f"summary-{self.study.pk}"]
        html = self.toggle(self.reading, on_page).content.decode()
        self.assertTrue(html.lstrip().startswith(f'<li id="row-{self.reading.pk}"'))
        # 上のタスクの ▶ の行：％ が変わる
        self.assertIn(f'<summary hx-swap-oob="true" id="summary-{self.study.pk}"', html)
        self.assertIn("サブタスク 2件・50%", html)
        # 文献調査が終わったので、発表スライドの 🔒 が外れる
        part = html[html.index(f'id="row-{self.slides.pk}"'):]
        self.assertNotIn("🔒", part)
        # ついでに書きかえる印は、いちばん外側の要素にだけ付く
        self.assertEqual(html.count("hx-swap-oob"), 2)

    def test_toggle_detail_returns_the_top_box(self):
        html = self.toggle(self.study, [f"detail-{self.study.pk}"]).content.decode()
        self.assertIn(f'id="detail-{self.study.pk}"', html)
        self.assertIn("未完了に戻す", html)
        self.assertIn("完了（", html)

    def test_toggle_needs_a_place_and_my_own_task(self):
        self.assertEqual(self.toggle(self.study, []).status_code, 404)
        others = Task.objects.create(owner=self.other, title="他の人のタスク")
        self.assertEqual(self.toggle(others, [f"card-{others.pk}"]).status_code, 404)
        others.refresh_from_db()
        self.assertFalse(others.is_done)

    def test_toggle_without_htmx_still_redirects(self):
        response = self.client.post(reverse("task_toggle", args=[self.study.pk]), {"next": reverse("home")})
        self.assertRedirects(response, reverse("home"))

    def test_children_are_loaded_when_opened(self):
        url = reverse("task_children", args=[self.study.pk])
        response = self.client.get(url)
        self.assertContains(response, f'id="row-{self.reading.pk}"')
        self.assertContains(response, f'id="row-{self.slides.pk}"')
        self.assertContains(response, "＋ サブタスク")
        # 何件あっても、データベースへの問い合わせの回数は同じ
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as before:
            self.client.get(url)
        for i in range(5):
            Task.objects.create(owner=self.me, title=f"追加{i}", parent=self.study)
        with self.assertNumQueries(len(before)):
            self.client.get(url)
        # 3段目の下には ＋ サブタスク を出さない
        chapter = Task.objects.create(owner=self.me, title="1章", parent=self.reading)
        Task.objects.create(owner=self.me, title="1節", parent=chapter)
        self.assertNotContains(self.client.get(reverse("task_children", args=[chapter.pk])), "＋ サブタスク")

    def test_children_are_mine_only(self):
        others = Task.objects.create(owner=self.other, title="他の人のタスク")
        self.assertEqual(self.client.get(reverse("task_children", args=[others.pk])).status_code, 404)
        self.client.logout()
        url = reverse("task_children", args=[self.study.pk])
        self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")

    def test_delete_source_returns_the_list_and_count(self):
        keep = Source.objects.create(task=self.study, kind="verbal", memo="残すメモ")
        gone = Source.objects.create(task=self.study, kind="verbal", memo="消すメモ")
        html = self.client.post(reverse("source_delete", args=[gone.pk]), **self.HX).content.decode()
        self.assertTrue(html.lstrip().startswith('<div id="sources-list"'))
        self.assertIn("残すメモ", html)
        self.assertNotIn("消すメモ", html)
        self.assertIn('<span id="sources-count" hx-swap-oob="innerHTML">1</span>', html)
        self.assertTrue(Source.objects.filter(pk=keep.pk).exists())

    def test_unlink_related_returns_the_related_box(self):
        self.study.related.add(self.slides)
        data = {"relation": "related", "target": self.slides.pk}
        html = self.client.post(reverse("task_unlink", args=[self.study.pk]), data, **self.HX).content.decode()
        self.assertTrue(html.lstrip().startswith(f'<div id="related-{self.study.pk}"'))
        self.assertIn("🔗 なし", html)
        self.assertFalse(self.study.related.exists())
