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
        # 子タスクは別のカードにしない（上の open_tasks が1件）。親のカードの中に1回だけ出る
        self.assertContains(response, ">薬理学の問題集</a>", count=1)
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
        self.exam = Task.objects.create(owner=self.me, title="国家試験", category="exam")
        self.client.force_login(self.me)

    def test_detail_shows_task_and_children(self):
        Task.objects.create(owner=self.me, title="薬理学", parent=self.exam)
        response = self.client.get(reverse("task_detail", args=[self.exam.pk]))
        self.assertContains(response, "国家試験")
        self.assertContains(response, "子タスク（1件）")
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
        self.client.post(reverse("task_update", args=[others.pk]), {"title": "書きかえ", "importance": 2, "category": "class"})
        others.refresh_from_db()
        self.assertEqual(others.title, "他の人のタスク")

    def test_edit_task(self):
        url = reverse("task_update", args=[self.exam.pk])
        self.assertContains(self.client.get(url), 'value="国家試験"')  # 今の内容が入った状態で開く
        response = self.client.post(
            url, {"title": "看護師国家試験", "importance": 3, "category": "exam", "due_at": "2027-02-14T09:00"}, follow=True
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
            url, {"title": "薬理学", "parent": self.exam.pk, "importance": 2, "category": "exam"}, follow=True
        )
        self.assertRedirects(response, reverse("task_detail", args=[self.exam.pk]))
        child = Task.objects.get(title="薬理学")
        self.assertEqual(child.parent, self.exam)
        self.assertEqual(child.owner, self.me)

    def test_cannot_add_fourth_level(self):
        subject = Task.objects.create(owner=self.me, title="薬理学", parent=self.exam)
        chapter = Task.objects.create(owner=self.me, title="第1章", parent=subject)
        detail = self.client.get(reverse("task_detail", args=[chapter.pk]))
        self.assertNotContains(detail, reverse("task_create_child", args=[chapter.pk]))
        self.assertContains(detail, "これ以上子タスクを付けられません")
        # URLを直接開いても作れず、詳細画面に戻される
        url = reverse("task_create_child", args=[chapter.pk])
        response = self.client.post(url, {"title": "4段目", "importance": 2, "category": "exam"})
        self.assertRedirects(response, reverse("task_detail", args=[chapter.pk]))
        self.assertFalse(Task.objects.filter(title="4段目").exists())

    def test_third_level_task_cannot_be_chosen_as_parent(self):
        # 3段目のタスクは親の選択肢に出ず、番号を直接送っても「正しくない選択」になる
        from .forms import TaskForm
        subject = Task.objects.create(owner=self.me, title="薬理学", parent=self.exam)
        chapter = Task.objects.create(owner=self.me, title="第1章", parent=subject)
        form = TaskForm(
            data={"title": "4段目", "parent": chapter.pk, "importance": 2, "category": "exam"},
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
        # 子・孫の名前がカードの中（開く部分）に入っていて、それぞれ切り替えボタンと詳細へのリンクがある
        self.assertContains(response, "子タスク 1件・0%")
        self.assertContains(response, "子タスク 1件・100%")
        self.assertContains(response, "薬理学")
        self.assertContains(response, "問題集 第1章")
        self.assertContains(response, reverse("task_toggle", args=[subject.pk]))
        self.assertContains(response, reverse("task_detail", args=[subject.pk]))
        self.assertContains(response, "<details", count=2)

    def test_card_without_children_has_no_toggle(self):
        Task.objects.create(owner=self.me, title="履修登録")
        response = self.client.get(reverse("home"))
        self.assertNotContains(response, "<details>")

    def test_home_query_count_does_not_grow_with_children(self):
        # 子タスクが増えても、データベースへの問い合わせの回数は増えない（まとめて読み込んでいる）
        exam = Task.objects.create(owner=self.me, title="国家試験")
        for i in range(5):
            sub = Task.objects.create(owner=self.me, title=f"科目{i}", parent=exam)
            Task.objects.create(owner=self.me, title=f"章{i}", parent=sub)
        self.client.get(reverse("home"))  # 1回目はセッションなどの準備があるので数えない
        with self.assertNumQueries(6):
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
        self.assertEqual(labels, ["なし（一番上のタスク）", "国家試験", "　└ 薬理学"])

    def test_create_child_from_menu(self):
        response = self.client.post(
            reverse("task_create"),
            {"title": "問題集 第2章", "parent": self.subject.pk, "importance": 2, "category": "exam"},
        )
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(Task.objects.get(title="問題集 第2章").parent, self.subject)

    def test_cannot_choose_other_users_task(self):
        response = self.client.post(
            reverse("task_create"),
            {"title": "なりすまし", "parent": self.others.pk, "importance": 2, "category": "class"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Task.objects.filter(title="なりすまし").exists())

    def test_edit_hides_self_descendants_and_too_deep(self):
        # 薬理学（下に1段ある）を編集するとき：自分と子孫は出ない。2段目の下に付けると4段になるので出ない
        other_top = Task.objects.create(owner=self.me, title="卒業研究")
        Task.objects.create(owner=self.me, title="文献調査", parent=other_top)
        labels = self.choices(self.client.get(reverse("task_update", args=[self.subject.pk])))
        self.assertEqual(labels, ["なし（一番上のタスク）", "国家試験", "卒業研究"])

    def test_move_task_to_another_parent(self):
        other_top = Task.objects.create(owner=self.me, title="卒業研究")
        self.client.post(
            reverse("task_update", args=[self.chapter.pk]),
            {"title": "第1章", "parent": other_top.pk, "importance": 2, "category": "exam"},
        )
        self.chapter.refresh_from_db()
        self.assertEqual(self.chapter.parent, other_top)

    def test_add_child_link_inside_card(self):
        response = self.client.get(reverse("home"))
        # 1段目・2段目の中には「＋ 子タスクを追加」があり、3段目の下にはない
        self.assertContains(response, reverse("task_create_child", args=[self.exam.pk]))
        self.assertContains(response, reverse("task_create_child", args=[self.subject.pk]))
        self.assertNotContains(response, reverse("task_create_child", args=[self.chapter.pk]))

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
