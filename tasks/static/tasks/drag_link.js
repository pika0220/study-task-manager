// カードを長押しして持ち上げ、別のカードに重ねて落とすと「つなぎ方」を選ぶ画面を出す（Issue #29）。
//
// 流れ：
//   1. [data-task-id] の付いた要素（カード・子タスクの行）を長押し（LONG_PRESS ミリ秒）すると持ち上がる
//   2. 指（マウス）についてくる「影」を出し、下にあるカードを光らせる
//   3. 離したら、サーバーに「どのつなぎ方ができるか」を聞き（task_link_options）、選ぶ画面を開く
//   4. 選んだボタンでフォームを送る（task_link）。保存とルールのチェックはサーバーが行う
//
// 長押しにしているのは、スマホでふつうに指を動かしたときの「画面のスクロール」とぶつからないようにするため。
(() => {
  const LONG_PRESS = 350; // 何ミリ秒押し続けたら持ち上げるか
  const MOVE_TOLERANCE = 8; // 長押しの途中で、これ以上指が動いたらスクロールとみなしてやめる（px）

  const dialog = document.getElementById("link-dialog");
  if (!dialog) return;
  const optionsUrlTemplate = dialog.dataset.optionsUrl; // 例：/tasks/0/link/options/
  const linkUrlTemplate = dialog.dataset.linkUrl; // 例：/tasks/0/link/
  const urlFor = (template, id) => template.replace("/0/", `/${id}/`);

  let timer = null; // 長押しを待つタイマー
  let start = null; // 押しはじめた位置
  let source = null; // 持ち上げたカード
  let dragging = false;
  let ghost = null; // 指についてくる影
  let over = null; // 今、下にあるカード
  let suppressClick = false; // 落としたあとの「クリック」でリンクが開かないようにする

  const cancelPress = () => {
    clearTimeout(timer);
    timer = null;
  };

  const cleanup = () => {
    cancelPress();
    if (ghost) ghost.remove();
    if (over) over.classList.remove("drop-target");
    if (source) source.classList.remove("drag-source");
    document.body.classList.remove("is-dragging");
    ghost = over = source = null;
    dragging = false;
  };

  const beginDrag = (x, y) => {
    dragging = true;
    document.body.classList.add("is-dragging");
    source.classList.add("drag-source");
    if (navigator.vibrate) navigator.vibrate(15); // スマホなら軽くふるえて、持ち上がったことを知らせる
    const rect = source.getBoundingClientRect();
    ghost = document.createElement("div");
    ghost.className = "drag-ghost";
    ghost.textContent = source.dataset.title;
    ghost.style.width = `${Math.min(rect.width, 260)}px`;
    document.body.appendChild(ghost);
    moveGhost(x, y);
  };

  const moveGhost = (x, y) => {
    ghost.style.left = `${x}px`;
    ghost.style.top = `${y}px`;
    // 影の下にある要素を調べ、いちばん内側の [data-task-id] を落とす先の候補にする
    const under = document.elementFromPoint(x, y);
    const target = under ? under.closest("[data-task-id]") : null;
    const next = target && target !== source ? target : null;
    if (next !== over) {
      if (over) over.classList.remove("drop-target");
      over = next;
      if (over) over.classList.add("drop-target");
    }
  };

  document.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return; // 左クリック・指だけ
    const item = event.target.closest("[data-task-id]");
    if (!item || event.target.closest("button, input, select, textarea, summary")) return;
    source = item;
    start = { x: event.clientX, y: event.clientY };
    cancelPress();
    timer = setTimeout(() => beginDrag(start.x, start.y), LONG_PRESS);
  });

  document.addEventListener("pointermove", (event) => {
    if (dragging) {
      moveGhost(event.clientX, event.clientY);
    } else if (timer && Math.hypot(event.clientX - start.x, event.clientY - start.y) > MOVE_TOLERANCE) {
      cancelPress(); // 長押しの前に指が動いた ＝ スクロールしたい
      source = null;
    }
  });

  document.addEventListener("pointerup", () => {
    if (!dragging) {
      cancelPress();
      source = null;
      return;
    }
    const from = source;
    const to = over;
    // 指を離した直後に来る「クリック」だけを止める。スマホではドラッグのあとにクリックが来ないことが多いので、
    // 0.5秒たったら止めるのをやめる（ずっと止めたままだと、次に押したボタンが効かなくなる）
    suppressClick = true;
    setTimeout(() => (suppressClick = false), 500);
    cleanup();
    if (to) openDialog(from, to);
  });

  document.addEventListener("pointercancel", cleanup);

  // 持ち上げている間は、スマホの画面がスクロールしないようにする
  document.addEventListener(
    "touchmove",
    (event) => {
      if (dragging) event.preventDefault();
    },
    { passive: false },
  );
  // 長押しで出る「メニュー」（リンクのプレビューなど）を出さない
  document.addEventListener("contextmenu", (event) => {
    if (timer || dragging) event.preventDefault();
  });
  // 落としたすぐあとのクリックで、タイトルのリンクが開かないようにする
  document.addEventListener(
    "click",
    (event) => {
      if (suppressClick && !dialog.contains(event.target)) {
        event.preventDefault();
        event.stopPropagation();
        suppressClick = false;
      }
    },
    true,
  );

  // ---- 選ぶ画面 ----
  const setChoice = (relation, info) => {
    const button = dialog.querySelector(`button[value="${relation}"]`);
    const reason = dialog.querySelector(`[data-reason="${relation}"]`);
    button.disabled = !info.ok;
    reason.textContent = info.ok ? "" : info.reason;
  };

  const openDialog = async (from, to) => {
    const sourceId = from.dataset.taskId;
    const targetId = to.dataset.taskId;
    const form = dialog.querySelector("form.link-form");
    form.action = urlFor(linkUrlTemplate, sourceId);
    form.querySelector('input[name="target"]').value = targetId;
    dialog.querySelectorAll("[data-source-title]").forEach((el) => (el.textContent = from.dataset.title));
    dialog.querySelectorAll("[data-target-title]").forEach((el) => (el.textContent = to.dataset.title));
    ["child", "after"].forEach((relation) => setChoice(relation, { ok: false, reason: "確かめています…" }));
    dialog.showModal();
    try {
      const response = await fetch(`${urlFor(optionsUrlTemplate, sourceId)}?target=${targetId}`, {
        headers: { Accept: "application/json" },
      });
      const data = await response.json();
      setChoice("child", data.child);
      setChoice("after", data.after);
    } catch (error) {
      ["child", "after"].forEach((relation) =>
        setChoice(relation, { ok: false, reason: "うまく確かめられませんでした．もう一度ためしてください．" }),
      );
    }
  };
})();
