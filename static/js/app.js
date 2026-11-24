// 全画面で使う、htmx（Issue #57）のための小さな部品。
//
// htmx：hx-post などの印が付いたフォームを、ページを読み直さずに送り、返ってきた HTML で画面の一部だけを書きかえる。
// このファイルでは、htmx だけではできない次のことをする：
//   1. ○ を押した瞬間に見た目を ✓ に変える（サーバーの返事を待たない。遠いサーバーでも軽く感じるように）
//   2. 失敗したら見た目を元に戻し、「保存できませんでした」と出す
//   3. 送るときに、今の画面にある書きかえ先（card-○・row-○ など）と戻り先（?back=）をサーバーに伝える
//   4. 書きかえたあとも、開いていた ▶（サブタスク）を開いたままにする
(() => {
  const isToggle = (el) => el instanceof Element && el.matches(".toggle-form");

  // ---- 1. ○ を押した瞬間に見た目を変える ----
  // data-check の付いた丸いボタン：○ ⇔ ✓。そのほか（詳細の「完了にする」）：返事が来るまで押せない見た目にする
  const flip = (form) => {
    const button = form.querySelector("button");
    if (!button) return;
    if (button.hasAttribute("data-check")) {
      const done = button.classList.contains("btn-success");
      button.classList.toggle("btn-success", !done);
      button.classList.toggle("btn-outline", done);
      button.textContent = done ? "" : "✓";
    } else {
      button.classList.toggle("btn-disabled");
    }
  };
  document.addEventListener("htmx:beforeRequest", (event) => {
    if (isToggle(event.detail.elt)) flip(event.detail.elt);
  });

  // ---- 2. 失敗したら元に戻す ----
  const showToast = (text) => {
    const toast = document.createElement("div");
    toast.setAttribute("role", "alert");
    toast.className = "alert alert-error fixed inset-x-4 bottom-24 z-50 mx-auto max-w-md text-sm shadow-lg";
    toast.textContent = text;
    document.body.append(toast);
    setTimeout(() => toast.remove(), 4000);
  };
  const failed = (event) => {
    if (isToggle(event.detail.elt)) flip(event.detail.elt);
    showToast("保存できませんでした．通信を確かめて，もう一度ためしてください．");
  };
  document.addEventListener("htmx:responseError", failed); // サーバーがエラーを返した
  document.addEventListener("htmx:sendError", failed); // 通信できなかった（電波が弱い など）

  // ---- 3. 今の画面にある書きかえ先と、戻り先を一緒に送る ----
  document.addEventListener("htmx:configRequest", (event) => {
    if (!isToggle(event.detail.elt)) return;
    const ids = [...document.querySelectorAll("[data-swap-id]")].map((el) => el.dataset.swapId);
    event.detail.parameters.on_page = ids.join(",");
    const back = new URLSearchParams(window.location.search).get("back");
    if (back) event.detail.parameters.back = back;
  });

  // ---- 4. 書きかえても、開いていた ▶ は開いたままにする ----
  // 書きかえる前に、その中で開いている details の id を覚えておき、書きかえたあとで開き直す。
  // 開き直すと、中身（サブタスクの行）は htmx がもう一度読みこむ（hx-trigger="toggle once"）
  const openIds = new Set();
  const remember = (el) => {
    if (!(el instanceof Element)) return;
    el.querySelectorAll("details[open][id]").forEach((details) => openIds.add(details.id));
  };
  document.addEventListener("htmx:beforeSwap", (event) => remember(event.detail.target));
  document.addEventListener("htmx:oobBeforeSwap", (event) => remember(event.detail.target));
  document.addEventListener("htmx:afterSettle", () => {
    openIds.forEach((id) => {
      const details = document.getElementById(id);
      if (details && !details.open) details.open = true;
    });
    openIds.clear();
  });
})();
