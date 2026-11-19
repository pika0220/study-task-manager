from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Task


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
        self.assertContains(response, "子タスク 1件・0%")
        # 子タスクは別のカードにせず、ほかの人のタスクは出さない
        self.assertNotContains(response, "薬理学の問題集")
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
            self.url, {"title": "実習記録を書く", "importance": 2, "category": "practicum"}, follow=True
        )
        self.assertRedirects(response, reverse("home"))
        task = Task.objects.get(title="実習記録を書く")
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
            {"title": "なりすまし", "importance": 2, "category": "class", "owner": self.other.pk},
        )
        self.assertEqual(Task.objects.get(title="なりすまし").owner, self.me)

    def test_due_at_from_datetime_local_input(self):
        # スマホの日時入力（datetime-local）が送ってくる形で受け取れる
        self.client.force_login(self.me)
        self.client.post(
            self.url,
            {"title": "レポート", "importance": 3, "category": "class", "due_at": "2026-12-01T18:00"},
        )
        due = timezone.localtime(Task.objects.get(title="レポート").due_at)
        self.assertEqual((due.month, due.day, due.hour, due.minute), (12, 1, 18, 0))

    def test_title_is_required(self):
        self.client.force_login(self.me)
        response = self.client.post(self.url, {"title": "", "importance": 2, "category": "class"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Task.objects.exists())
        self.assertContains(response, "このフィールドは必須です")

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
