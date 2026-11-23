// マインドマップ（Issue #30・#36）。分類ごとの「部屋」に、棚から選んだタスクを置き、つながりを線で描く。
//
// できること：
//   - 上のメニュー            → 別の部屋（分類）に切り替える
//   - 📦                      → 棚を開く（置く・しまう）
//   - 点を押す                → そのタスクの詳細画面へ
//   - 点をなぞる              → 点を動かす（離したら置き場所をサーバーに保存）
//   - 点の右の ● から別の点へ → 線を引いて「つなぎ方」を選ぶ（ホームのドラッグと同じ画面・同じルール）
//   - 線を押す                → つなぎ方を切り替える（📁 ⇔ 🔒）か、つながりを外す
//   - ＋ タスク               → 名前・どのタスクに・つなぎ方を選んで足す（場所はサーバーが空いているところに決める）
//   - 何もないところをなぞる  → ふつうのスクロールでマップを動かす
//   - 縮小・全体・拡大        → 小さくする・全部が見える大きさ・大きくする
(() => {
  const addDialog = document.getElementById("map-add-dialog");
  const addForm = addDialog.querySelector('form[method="post"]');
  const MAX_DEPTH = 3; // 入れ子は3段まで（モデルの Task.MAX_DEPTH と同じ）

  // ---- タスクを足す画面 ----
  const anchorSelect = addForm.querySelector('select[name="anchor"]');
  const relationField = addForm.querySelector("[data-relation-field]");
  const relationNote = addForm.querySelector("[data-relation-note]");
  const radio = (value) => addForm.querySelector(`input[name="relation"][value="${value}"]`);

  // 選んだタスクに合わせて、つなぎ方の欄を見せる・押せなくする（最後のチェックはサーバーがする）
  //   - 「つながない」なら、つなぎ方は聞かない（いちばん上に置く）
  //   - 3段目のタスクの「中」には入れられない（入れ子は3段まで）
  const updateRelations = () => {
    const option = anchorSelect.selectedOptions[0];
    const level = Number(option.dataset.level);
    relationField.hidden = level === 0;
    const childOk = level < MAX_DEPTH;
    radio("child").disabled = !childOk;
    if (!childOk) radio("after").checked = true;
    relationNote.textContent = childOk ? "" : "3段目 → 📁 は不可（🔒 でつなぐ）";
  };
  anchorSelect.addEventListener("change", updateRelations);

  const openAdd = () => {
    addForm.reset(); // 前に入れかけた名前を消し、「つながない」に戻す
    updateRelations();
    addDialog.showModal();
    addForm.querySelector('input[name="title"]').focus();
  };
  document.querySelectorAll("[data-add-task]").forEach((button) => button.addEventListener("click", openAdd));

  // ---- 棚 ----
  const shelfDialog = document.getElementById("map-shelf-dialog");
  document.querySelectorAll("[data-open-shelf]").forEach((button) =>
    button.addEventListener("click", () => shelfDialog.showModal()),
  );

  // 部屋の切り替え：選んだ部屋の画面へ移る
  const select = document.getElementById("map-select");
  if (select) select.addEventListener("change", () => (window.location.href = select.value));

  const viewport = document.getElementById("map-viewport");
  const sizer = document.getElementById("map-sizer");
  const canvas = document.getElementById("map-canvas");
  const svg = document.getElementById("map-lines");
  const edgeLayer = document.getElementById("map-edges");
  const tempLine = document.getElementById("map-temp-line");
  const status = document.getElementById("map-status");
  const unlinkDialog = document.getElementById("map-unlink-dialog");
  const nodes = JSON.parse(document.getElementById("map-data").textContent);
  const roomKey = viewport.dataset.roomKey; // この部屋の名前（分類の番号、分類なしは none）
  const positionUrlTemplate = viewport.dataset.positionUrl; // 例：/tasks/0/position/
  const unlinkUrlTemplate = unlinkDialog.dataset.unlinkUrl; // 例：/tasks/0/unlink/
  const relinkUrlTemplate = unlinkDialog.dataset.relinkUrl; // 例：/tasks/0/relink/
  const urlFor = (template, id) => template.replace("/0/", `/${id}/`);
  // フォームに入っている「合言葉」（CSRFトークン）。JavaScript から送るときにも付ける
  const csrfToken = addForm.querySelector('input[name="csrfmiddlewaretoken"]').value;

  const SVG_NS = "http://www.w3.org/2000/svg";
  const MOVE_TOLERANCE = 6; // これ以上動いたら「押した」ではなく「なぞった」とみなす（px）
  const LIMIT = 5000; // 置ける場所の上限（サーバーの MAP_LIMIT と同じ）
  const MARGIN = 400; // 紙の右と下に残しておく余白
  const MIN_SCALE = 0.4; // いちばん小さくしたときの倍率
  const MAX_SCALE = 1.6; // いちばん大きくしたときの倍率
  const BUTTONS_HEIGHT = 60; // 窓の下に重なっているボタン（＋ タスク・縮小など）の高さ。点がその下に隠れないように空ける
  const clamp = (value) => Math.min(Math.max(value, 0), LIMIT);
  let scale = 1; // 今の倍率（1 ＝ そのままの大きさ）

  // ---- 点を置く ----
  const byId = new Map();
  nodes.forEach((node) => {
    const el = document.createElement("div");
    el.className = [
      "map-node",
      `importance-${node.importance}`,
      node.top ? "is-top" : "",
      node.locked ? "is-locked" : "",
      node.done ? "is-done" : "",
    ].join(" ");
    el.dataset.taskId = node.id;
    el.dataset.title = node.title;
    const title = document.createElement("span");
    // textContent：タスク名を「文字」として入れる（HTMLとして読まれないので安全）
    title.textContent = node.locked ? `🔒 ${node.title}` : node.title;
    el.append(title);
    const handle = document.createElement("span");
    handle.className = "map-handle";
    handle.title = "ここから別の点へ線を引く";
    el.append(handle);
    canvas.appendChild(el);
    node.el = el;
    byId.set(node.id, node);
  });

  const place = (node) => {
    node.el.style.left = `${node.x}px`;
    node.el.style.top = `${node.y}px`;
  };
  nodes.forEach((node) => {
    place(node);
    node.w = node.el.offsetWidth;
    node.h = node.el.offsetHeight;
  });

  // ---- 窓の高さ ----
  // 窓の上にお知らせ（「足しました」など）が出ると、窓の始まる位置が下がる。
  // 実際に始まる位置から、下のメニュー（約80px）の上までを窓の高さにする
  const fitViewportHeight = () => {
    const top = viewport.getBoundingClientRect().top + window.scrollY;
    viewport.style.height = `${Math.max(320, window.innerHeight - top - 80)}px`;
  };
  fitViewportHeight();
  window.addEventListener("resize", fitViewportHeight);

  // ---- 紙の大きさと倍率 ----
  let canvasWidth = 0;
  let canvasHeight = 0;
  const resizeCanvas = () => {
    // 紙の大きさ：いちばん右下の点＋余白。今の倍率で窓より小さくはしない
    canvasWidth = Math.max(viewport.clientWidth / scale, ...nodes.map((n) => n.x + n.w + MARGIN));
    canvasHeight = Math.max(viewport.clientHeight / scale, ...nodes.map((n) => n.y + n.h + MARGIN));
    canvas.style.width = `${canvasWidth}px`;
    canvas.style.height = `${canvasHeight}px`;
    svg.setAttribute("width", canvasWidth);
    svg.setAttribute("height", canvasHeight);
    // 紙は transform で拡大・縮小する。見た目の大きさは変わるが、場所の計算はもとの大きさのまま
    canvas.style.transform = `scale(${scale})`;
    // sizer は「拡大・縮小したあとの大きさ」。窓は、この大きさに合わせてスクロールする
    sizer.style.width = `${canvasWidth * scale}px`;
    sizer.style.height = `${canvasHeight * scale}px`;
  };

  // 倍率を変える。（x, y）：変えたあとも窓の真ん中に見えていてほしい、紙の上の場所
  const setScale = (next, x, y) => {
    scale = Math.min(Math.max(next, MIN_SCALE), MAX_SCALE);
    resizeCanvas();
    viewport.scrollLeft = x * scale - viewport.clientWidth / 2;
    viewport.scrollTop = y * scale - viewport.clientHeight / 2;
  };
  const viewCenter = () => [
    (viewport.scrollLeft + viewport.clientWidth / 2) / scale,
    (viewport.scrollTop + viewport.clientHeight / 2) / scale,
  ];
  // 渡した点（list）が全部窓に入る倍率にして、真ん中に見せる
  const fitNodes = (list) => {
    const left = Math.min(...list.map((n) => n.x));
    const top = Math.min(...list.map((n) => n.y));
    const right = Math.max(...list.map((n) => n.x + n.w));
    const bottom = Math.max(...list.map((n) => n.y + n.h));
    const padding = 40;
    const buttons = BUTTONS_HEIGHT;
    const fit = Math.min(
      viewport.clientWidth / (right - left + padding * 2),
      (viewport.clientHeight - buttons) / (bottom - top + padding * 2),
      1, // 点が少ないときは、大きくしすぎない
    );
    // ボタンの分だけ、見せる場所を下にずらす（＝点が少し上に寄る）
    setScale(fit, (left + right) / 2, (top + bottom) / 2 + buttons / 2 / Math.max(fit, MIN_SCALE));
  };
  const fitAll = () => {
    if (!nodes.length) {
      // まだ何も置いていない部屋：最初の点が置かれる、紙の真ん中あたりを見せる（サーバーの MAP_CENTER）
      setScale(1, 800, 800);
      return;
    }
    fitNodes(nodes);
  };
  document.querySelectorAll("[data-zoom]").forEach((button) =>
    button.addEventListener("click", () => {
      const kind = button.dataset.zoom;
      if (kind === "fit") return fitAll();
      setScale(scale * (kind === "in" ? 1.25 : 0.8), ...viewCenter());
    }),
  );

  // ---- 線を描く ----
  // relation："child"（from は to のサブタスク）／"after"（from は to が終わってから）
  const edges = [];
  nodes.forEach((node) => {
    // 先にやること（同じ親の中のタスク）の先に描くタスクは、親との線を引かない（🔒 の線だけでつながって見える）
    if (node.parent && byId.has(node.parent) && !node.chained) {
      edges.push({ relation: "child", from: node, to: byId.get(node.parent) });
    }
    if (node.prerequisite && byId.has(node.prerequisite)) {
      edges.push({ relation: "after", from: node, to: byId.get(node.prerequisite) });
    }
  });

  const center = (node) => [node.x + node.w / 2, node.y + node.h / 2];
  // 点の中心から、相手の方向へ進んで「点のふち」にあたる場所（矢印が点に隠れないように）
  const border = (node, [tx, ty]) => {
    const [cx, cy] = center(node);
    const dx = tx - cx;
    const dy = ty - cy;
    if (!dx && !dy) return [cx, cy];
    const ratio = 1 / Math.max(Math.abs(dx) / (node.w / 2 + 6), Math.abs(dy) / (node.h / 2 + 6));
    return ratio < 1 ? [cx + dx * ratio, cy + dy * ratio] : [cx, cy];
  };

  const svgElement = (name, attrs) => {
    const el = document.createElementNS(SVG_NS, name);
    Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, value));
    return el;
  };

  const drawEdges = () => {
    edgeLayer.replaceChildren();
    edges.forEach((edge) => {
      // 線は to から from へ引く。📁：親 → 子　🔒：先にやること → あとでやること（矢印の向き）
      const [x1, y1] = border(edge.to, center(edge.from));
      const [x2, y2] = border(edge.from, center(edge.to));
      const group = svgElement("g", { class: "map-edge" });
      const isAfter = edge.relation === "after";
      // 📁（親子）はまっすぐ。🔒 は横にふくらませた曲線にして、親子の線と重ならないようにする
      // （曲がり具合を決める点（mx, my）を、線のまん中から直角の向きに、長さの 0.45 倍だけずらす）
      // ただし、親との線を引かないタスク（chained）への 🔒 は、重なる線がないのでまっすぐにする
      const bend = isAfter && !edge.from.chained ? 0.45 : 0;
      const mx = (x1 + x2) / 2 - (y2 - y1) * bend;
      const my = (y1 + y2) / 2 + (x2 - x1) * bend;
      const d = `M ${x1} ${y1} Q ${mx} ${my} ${x2} ${y2}`; // Q：なめらかな曲線（2次ベジェ曲線）
      group.append(
        svgElement("path", { d, class: "map-edge-hit" }),
        svgElement("path", {
          d,
          class: `map-edge-line${isAfter ? " is-after" : ""}`,
          ...(isAfter ? { "marker-end": "url(#map-arrow)" } : {}),
        }),
      );
      // 📁 は親子の線すべてに付くとうるさいので、🔒（前後のつながり）にだけ印を付ける
      if (isAfter) {
        // 曲線のまん中（はじめ・曲がり具合の点・おわりの平均）に置く
        const label = svgElement("text", { x: (x1 + 2 * mx + x2) / 4, y: (y1 + 2 * my + y2) / 4, class: "map-edge-label" });
        label.textContent = "🔒";
        group.append(label);
      }
      group.addEventListener("click", () => openUnlink(edge));
      edgeLayer.append(group);
    });
  };

  // ---- 点の置き場所を保存する ----
  const savePosition = async (node) => {
    const body = new URLSearchParams({ x: Math.round(node.x), y: Math.round(node.y) });
    try {
      const response = await fetch(urlFor(positionUrlTemplate, node.id), {
        method: "POST",
        headers: { "X-CSRFToken": csrfToken },
        body,
      });
      if (!response.ok) throw new Error(response.status);
      status.textContent = "";
    } catch (error) {
      status.textContent = "置き場所を保存できませんでした．画面を読みこみ直してください．";
    }
  };

  // ---- 指（マウス）の動き ----
  let action = null; // 今していること：{ type: "press" | "move" | "line", node, ... }
  let over = null; // 線を引いているとき、下にある点
  let suppressClick = false; // なぞり終わった直後の「クリック」で、線の画面が開かないように

  // 画面の上の位置（clientX, clientY）を、紙の上の位置に直す（倍率で割る）
  const toCanvas = (event) => {
    const rect = canvas.getBoundingClientRect();
    return [(event.clientX - rect.left) / scale, (event.clientY - rect.top) / scale];
  };

  const setOver = (node) => {
    if (node === over) return;
    if (over) over.el.classList.remove("drop-target");
    over = node;
    if (over) over.el.classList.add("drop-target");
  };

  canvas.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    const el = event.target.closest(".map-node");
    if (!el) return; // 何もないところ：ふつうのスクロールにまかせる
    event.preventDefault(); // 文字が選ばれないようにする
    const node = byId.get(Number(el.dataset.taskId));
    const onHandle = Boolean(event.target.closest(".map-handle"));
    action = { type: onHandle ? "line" : "press", node, sx: event.clientX, sy: event.clientY, ox: node.x, oy: node.y };
    // 指が点の外に出ても、続きの動きをこの点で受け取る（できないブラウザでも、動きは canvas で受け取れる）
    try {
      el.setPointerCapture(event.pointerId);
    } catch (error) {
      // 受け取れなくても、そのまま続ける
    }
    if (onHandle) {
      const [x, y] = center(node);
      tempLine.setAttribute("x1", x);
      tempLine.setAttribute("y1", y);
      tempLine.setAttribute("x2", x);
      tempLine.setAttribute("y2", y);
      tempLine.classList.add("is-visible");
    }
  });

  canvas.addEventListener("pointermove", (event) => {
    if (!action) return;
    const dx = event.clientX - action.sx;
    const dy = event.clientY - action.sy;
    if (action.type === "press" && Math.hypot(dx, dy) > MOVE_TOLERANCE) {
      action.type = "move";
      action.node.el.classList.add("is-moving");
    }
    if (action.type === "move") {
      // 画面の上で動いた分を倍率で割ると、紙の上で動いた分になる
      action.node.x = clamp(action.ox + dx / scale);
      action.node.y = clamp(action.oy + dy / scale);
      place(action.node);
      drawEdges();
    } else if (action.type === "line") {
      const [x, y] = toCanvas(event);
      tempLine.setAttribute("x2", x);
      tempLine.setAttribute("y2", y);
      // 指の下にある点を探す（線は pointer-events: none なので、点が見つかる）
      const under = document.elementFromPoint(event.clientX, event.clientY);
      const target = under ? under.closest(".map-node") : null;
      const node = target ? byId.get(Number(target.dataset.taskId)) : null;
      setOver(node && node !== action.node ? node : null);
    }
  });

  const finish = (cancelled) => {
    const done = action;
    action = null;
    if (!done) return;
    if (done.type === "press") {
      if (!cancelled) {
        // 押しただけ：詳細画面へ。詳細の「← 🗺️ マップ」で戻ったときに同じ場所を見せるため、今の場所を覚えておく
        rememberView();
        window.location.href = done.node.url;
      }
      return;
    }
    suppressClick = true;
    setTimeout(() => (suppressClick = false), 500);
    if (done.type === "move") {
      done.node.el.classList.remove("is-moving");
      if (cancelled) {
        done.node.x = done.ox;
        done.node.y = done.oy;
        place(done.node);
        drawEdges();
      } else {
        resizeCanvas();
        savePosition(done.node);
      }
    } else if (done.type === "line") {
      tempLine.classList.remove("is-visible");
      const target = over;
      setOver(null);
      if (!cancelled && target && window.TaskLink) {
        // 「done.node を target のサブタスクにする／target が終わってからやる」を選ぶ画面
        window.TaskLink.open(done.node, target);
      }
    }
  };
  canvas.addEventListener("pointerup", () => finish(false));
  canvas.addEventListener("pointercancel", () => finish(true));

  // ---- 線を押したら、つながりを外すか聞く ----
  const openUnlink = (edge) => {
    if (suppressClick) return;
    const form = unlinkDialog.querySelector('form[method="post"]');
    form.querySelector('input[name="relation"]').value = edge.relation;
    const isChild = edge.relation === "child";
    const { from, to } = edge;
    unlinkDialog.querySelector("[data-unlink-text]").textContent = isChild
      ? `📁 「${from.title}」は「${to.title}」のサブタスク`
      : `🔒 「${from.title}」は「${to.title}」が終わってから`;

    // 切り替えるボタン：📁 なら 🔒 に、🔒 なら 📁 に
    const relinkButton = unlinkDialog.querySelector("[data-relink-button]");
    relinkButton.formAction = urlFor(relinkUrlTemplate, from.id);
    unlinkDialog.querySelector("[data-relink-label]").textContent = isChild
      ? `🔒 「${to.title}」が終わってからやる，に切り替える`
      : `📁 「${to.title}」のサブタスクにする，に切り替える`;
    relinkButton.disabled = false;
    unlinkDialog.querySelector("[data-relink-reason]").textContent = "";

    unlinkDialog.querySelector("[data-unlink-button]").formAction = urlFor(unlinkUrlTemplate, from.id);
    const parent = byId.get(from.parent);
    unlinkDialog.querySelector("[data-unlink-note]").textContent = isChild
      ? `外す → 「${from.title}」はこの部屋のいちばん上のタスクに（どちらもタスクは消えない）`
      : from.chained && parent
        ? `外す → 「${from.title}」は「${parent.title}」のサブタスクとして線でつながる（どちらもタスクは消えない）`
        : "どちらもタスクは消えない";
    unlinkDialog.showModal();
  };

  // ---- 画面を読みこみ直しても、見ていた場所と大きさに戻す ----
  // つないだ・外した・足したあと（画面が読みこみ直される）と、点を押して詳細画面へ行くときに覚えておく（マップごとに別々）
  const VIEW_KEY = `task-map-view-${roomKey}`;
  function rememberView() {
    // function で書くと、この行より上（点を押したとき）からも呼べる
    try {
      const [x, y] = viewCenter();
      sessionStorage.setItem(VIEW_KEY, JSON.stringify({ x, y, scale }));
    } catch (error) {
      // 保存できない設定のブラウザでは、覚えないだけ
    }
  }
  document.addEventListener(
    "submit",
    (event) => {
      if (event.target.dataset.noKeep !== undefined) return; // 並べ直すときは覚えない（全体が見える大きさで開く）
      rememberView();
    },
    true,
  );

  resizeCanvas();
  drawEdges();
  let saved = null;
  try {
    saved = JSON.parse(sessionStorage.getItem(VIEW_KEY));
    sessionStorage.removeItem(VIEW_KEY);
  } catch (error) {
    saved = null;
  }
  // 点が窓の中に見えているか（list のどれか1つでも見えていれば true）
  const anyVisible = (list) => {
    const [cx, cy] = viewCenter();
    const halfW = viewport.clientWidth / scale / 2;
    const halfH = viewport.clientHeight / scale / 2;
    return list.some(
      (n) => n.x + n.w > cx - halfW && n.x < cx + halfW && n.y + n.h > cy - halfH && n.y < cy + halfH,
    );
  };
  // 点（list）が全部見えるところまで、今の大きさのまま、なめらかにスクロールする。
  // 動く量はいちばん少なくする（下に置いた木なら、今見ていた木が上に少し残る）。
  // 今の大きさでは窓に入りきらないときだけ、入る大きさに縮める
  const showNodes = (list) => {
    const pad = 24; // 窓のふちとの間のすきま（画面の px）
    const left = Math.min(...list.map((n) => n.x)) * scale - pad;
    const top = Math.min(...list.map((n) => n.y)) * scale - pad;
    const right = Math.max(...list.map((n) => n.x + n.w)) * scale + pad;
    const bottom = Math.max(...list.map((n) => n.y + n.h)) * scale + pad;
    const width = viewport.clientWidth;
    const height = viewport.clientHeight - BUTTONS_HEIGHT; // 下のボタンに隠れる分は数えない
    if (right - left > width || bottom - top > height) {
      fitNodes(list);
      return;
    }
    // 今のスクロールの位置を、「list が全部見える範囲」におさめる
    const fit = (current, low, high, size) => Math.min(Math.max(current, high - size), low);
    viewport.scrollTo({
      left: fit(viewport.scrollLeft, left, right, width),
      top: fit(viewport.scrollTop, top, bottom, height),
      behavior: "smooth", // 一瞬で飛ばずに動くので、どこへ動いたかがわかる
    });
  };
  if (saved && Number.isFinite(saved.scale)) {
    setScale(saved.scale, saved.x, saved.y);
    // 覚えていた場所に、点が1つも見えないとき（何もない部屋で覚えた場所など）は、全体を見せる
    if (nodes.length && !anyVisible(nodes)) fitAll();
  } else {
    fitAll(); // 初めて開いたときは、全部が見える大きさで見せる
  }

  // ---- 置いた・足したタスクを見せる（?focus=番号）----
  // 棚から置いた木や、新しく足したタスクは、今見ている場所の外（下など）にあることがある。
  // その木（そのタスクとサブタスク）が窓に入るまでスクロールし、少しの間ふちを光らせる
  const params = new URLSearchParams(window.location.search);
  const focused = byId.get(Number(params.get("focus")));
  if (params.has("focus")) {
    // 読みこみ直したときに、もう一度動かないように、URL から ?focus= を消しておく
    params.delete("focus");
    const rest = params.toString();
    history.replaceState(null, "", window.location.pathname + (rest ? `?${rest}` : "") + window.location.hash);
  }
  if (focused) {
    // 木のいちばん上まで、親をたどる
    const rootOf = (node) => {
      let current = node;
      while (current.parent && byId.has(current.parent)) current = byId.get(current.parent);
      return current;
    };
    // 棚から置いた木（いちばん上のタスク）は木ごと、中に足したタスクはその点だけを見せる
    const target = focused.top ? nodes.filter((n) => rootOf(n) === focused) : [focused];
    showNodes(target);
    focused.el.classList.add("is-focus");
    window.setTimeout(() => focused.el.classList.remove("is-focus"), 2500);
  }
})();
