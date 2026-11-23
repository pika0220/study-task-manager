// 使い方（Issue #47）。guide.html のカード（.guide-slide）から、章のボタン・● ● ○・前へ／次へ を作って動かす。
//
// できること：
//   - 横にスワイプ          → 1枚ずつ進む・戻る（止める位置は CSS の scroll-snap が決める）
//   - 前へ／次へ・← →キー  → 1枚ずつ動かす
//   - ● を押す              → その1枚へ
//   - 章のボタンを押す      → その章の1枚目へ
//   - 最後の1枚             → 「次へ」の代わりに「🏠 はじめる」
//   - /guide/#map のように開く → その章（data-key="map"）の1枚目から（ヘルプの章のリンクで使う）
(() => {
  const track = document.querySelector("[data-guide-track]");
  if (!track) return;
  const slides = [...track.querySelectorAll(".guide-slide")];
  const dotsBox = document.querySelector("[data-guide-dots]");
  const chaptersBox = document.querySelector("[data-guide-chapters]");
  const prevButton = document.querySelector("[data-guide-prev]");
  const nextButton = document.querySelector("[data-guide-next]");
  const doneLink = document.querySelector("[data-guide-done]");

  // 何枚目のカードへ動かす（scroll-behavior: smooth なので、なめらかに動く）
  const goTo = (index, behavior = "smooth") => {
    const i = Math.min(Math.max(index, 0), slides.length - 1);
    track.scrollTo({ left: slides[i].offsetLeft - track.offsetLeft, behavior });
  };

  // ● ● ○：カード1枚に1つ
  const dots = slides.map((slide, i) => {
    const dot = document.createElement("button");
    dot.type = "button";
    dot.className = "guide-dot";
    dot.setAttribute("aria-label", `${i + 1}枚目`);
    dot.addEventListener("click", () => goTo(i));
    dotsBox.append(dot);
    // 読み上げのとき「3 / 8」のように伝える
    slide.setAttribute("role", "group");
    slide.setAttribute("aria-roledescription", "カード");
    slide.setAttribute("aria-label", `${i + 1} / ${slides.length}`);
    return dot;
  });

  // 章のボタン：章の名前ごとに1つ（並びはカードの順）
  const chapters = [];
  slides.forEach((slide, i) => {
    const name = slide.dataset.chapter;
    if (!chapters.some((c) => c.name === name)) chapters.push({ name, key: slide.dataset.key, first: i });
  });
  chapters.forEach((chapter) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-sm shrink-0 rounded-full";
    button.textContent = chapter.name;
    button.addEventListener("click", () => goTo(chapter.first));
    chaptersBox.append(button);
    chapter.button = button;
  });

  let current = -1;
  const show = (index) => {
    if (index === current) return;
    current = index;
    slides.forEach((slide, i) => slide.classList.toggle("is-active", i === index)); // 見えている1枚だけ、絵を動かす
    dots.forEach((dot, i) => {
      dot.classList.toggle("is-on", i === index);
      dot.setAttribute("aria-current", i === index ? "true" : "false");
    });
    const chapterName = slides[index].dataset.chapter;
    chapters.forEach((chapter) => {
      const on = chapter.name === chapterName;
      chapter.button.classList.toggle("btn-primary", on);
      if (on) chapter.button.scrollIntoView({ block: "nearest", inline: "nearest" });
    });
    prevButton.disabled = index === 0;
    const last = index === slides.length - 1;
    nextButton.hidden = last;
    doneLink.hidden = !last;
  };

  // 今どのカードが見えているかは、横のスクロール位置から計算する（スワイプでも、ボタンでも同じ）
  const update = () => show(Math.round(track.scrollLeft / track.clientWidth));
  track.addEventListener("scroll", () => window.requestAnimationFrame(update), { passive: true });
  window.addEventListener("resize", update);

  prevButton.addEventListener("click", () => goTo(current - 1));
  nextButton.addEventListener("click", () => goTo(current + 1));
  track.addEventListener("keydown", (event) => {
    if (event.key === "ArrowRight") goTo(current + 1);
    else if (event.key === "ArrowLeft") goTo(current - 1);
    else return;
    event.preventDefault();
  });

  // アドレスの # で章が決まっていれば、その章の1枚目から（なめらかに動かさず、すぐに）
  const startChapter = chapters.find((c) => `#${c.key}` === window.location.hash);
  if (startChapter) goTo(startChapter.first, "instant");
  update();
})();
