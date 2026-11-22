// 「つなぎ方」を選ぶ画面（_link_dialog.html）を開く部品。
// ホームのドラッグ（drag_link.js）と、マインドマップ（task_map.js）の両方から
//   window.TaskLink.open({ id, title }, { id, title })
// のように使う。1つ目のタスクを、2つ目のタスクに「中に入れる」か「終わってからやる」かを選ぶ。
window.TaskLink = (() => {
  const dialog = document.getElementById("link-dialog");
  if (!dialog) return null;
  const optionsUrlTemplate = dialog.dataset.optionsUrl; // 例：/tasks/0/link/options/
  const linkUrlTemplate = dialog.dataset.linkUrl; // 例：/tasks/0/link/
  const urlFor = (template, id) => template.replace("/0/", `/${id}/`);

  const setChoice = (relation, info) => {
    const button = dialog.querySelector(`button[value="${relation}"]`);
    const reason = dialog.querySelector(`[data-reason="${relation}"]`);
    button.disabled = !info.ok;
    reason.textContent = info.ok ? "" : info.reason;
  };

  const open = async (from, to) => {
    const form = dialog.querySelector("form.link-form");
    form.action = urlFor(linkUrlTemplate, from.id);
    form.querySelector('input[name="target"]').value = to.id;
    dialog.querySelectorAll("[data-source-title]").forEach((el) => (el.textContent = from.title));
    dialog.querySelectorAll("[data-target-title]").forEach((el) => (el.textContent = to.title));
    ["child", "after"].forEach((relation) => setChoice(relation, { ok: false, reason: "確かめています…" }));
    dialog.showModal();
    // どのつなぎ方ができるかは、サーバー（モデルのルール）に聞く
    try {
      const response = await fetch(`${urlFor(optionsUrlTemplate, from.id)}?target=${to.id}`, {
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

  return { open, dialog };
})();
