/* Hướng dẫn từng bước trong app (tour) và phím ? mở bảng phím tắt.
   Nội dung ở /static/tours.json: tour chọn theo body[data-screen], bước lọc theo body[data-role].
   Bước không tìm thấy phần tử (vai trò không có nút đó, chế độ chỉ xem) được bỏ qua.
   Bước có advanceOn chỉ sang bước sau khi người dùng làm đúng thao tác (bấm nút hoặc nhấn phím tắt).
   Lần đầu vào một màn hình có tour: hiện lời mời ở góc dưới; đã xem hoặc bấm "Để sau" thì không mời lại. */
(function () {
  'use strict';

  var body = document.body;
  var SCREEN = body.getAttribute('data-screen') || '';
  var ROLE = body.getAttribute('data-role') || 'none';
  var DONE_KEY = 'tour:done:' + SCREEN + ':' + ROLE;
  var CONTINUE_KEY = 'tour:continue';
  var NARROW = 768;      /* hẹp hơn: hộp hướng dẫn thành tấm dưới đáy màn hình */
  var NAV_OVERLAY = 1100; /* hẹp hơn: cây cấu trúc là lớp phủ, phải mở ra trước khi chỉ vào */

  function storage(kind, key, value) {
    try {
      var s = window[kind];
      if (value === undefined) return s.getItem(key);
      if (value === null) s.removeItem(key); else s.setItem(key, value);
    } catch (e) { /* trình duyệt chặn lưu trữ: tour vẫn chạy, chỉ không nhớ */ }
    return null;
  }
  function typing(el) {
    return el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
  }
  function make(tag, cls, text) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text) el.textContent = text;
    return el;
  }
  /* chữ trong tours.json: [[A]] thành phím <kbd>A</kbd>; không nhận HTML */
  function rich(target, text) {
    String(text || '').split(/(\[\[[^\]]+\]\])/).forEach(function (part) {
      var m = /^\[\[([^\]]+)\]\]$/.exec(part);
      target.appendChild(m ? make('kbd', '', m[1]) : document.createTextNode(part));
    });
    return target;
  }

  /* ---- Chọn tour và bước ---- */
  var all = [];
  function screenTour() {
    for (var i = 0; i < all.length; i++) {
      var t = all[i];
      if (t.screen === SCREEN && (!t.roles || t.roles.indexOf(ROLE) !== -1)) return t;
    }
    return null;
  }
  function expand(def, depth) {
    var out = [];
    (def.steps || []).forEach(function (step) {
      if (step.include) {
        if (depth > 3) return;
        all.forEach(function (t) { if (t.id === step.include) out = out.concat(expand(t, depth + 1)); });
      } else if (!step.roles || step.roles.indexOf(ROLE) !== -1) {
        out.push(step);
      }
    });
    return out;
  }
  function visible(el) {
    if (!el || !el.isConnected || el.closest('[hidden]')) return false;
    var r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }
  /* null: bước không neo (hộp ở giữa màn hình); undefined: không có phần tử, bỏ qua bước */
  function target(step) {
    if (!step.selector) return null;
    if (step.navOpen && window.innerWidth <= NAV_OVERLAY) body.classList.add('nav-open');
    var el = document.querySelector(step.selector);
    return visible(el) || (el && step.navOpen && window.innerWidth <= NAV_OVERLAY) ? el : undefined;
  }

  /* ---- Giao diện tour ---- */
  var run = null;  /* {def, steps, index, el, returnFocus, openedNav, off} */
  var ui = null;

  function buildUI() {
    var block = make('div', 'tour-block');
    var spot = make('div', 'tour-spot');
    var pop = make('div', 'tour-pop');
    pop.setAttribute('role', 'dialog');
    pop.setAttribute('aria-modal', 'false');
    pop.setAttribute('aria-labelledby', 'tour-title');
    pop.setAttribute('aria-describedby', 'tour-text');
    pop.tabIndex = -1;
    var head = make('div', 'tour-head');
    var count = make('span', 'tour-count');
    var close = make('button', 'icon-btn tour-close', '×');
    close.type = 'button';
    close.setAttribute('aria-label', 'Đóng hướng dẫn');
    close.title = 'Đóng hướng dẫn (Esc)';
    head.appendChild(count);
    head.appendChild(close);
    var title = make('h2', 'tour-title');
    title.id = 'tour-title';
    var text = make('div', 'tour-text');
    text.id = 'tour-text';
    var task = make('p', 'tour-task');
    task.setAttribute('role', 'status');
    var foot = make('div', 'tour-foot');
    var prev = make('button', 'btn', 'Trước');
    var next = make('button', 'btn primary', 'Tiếp');
    prev.type = next.type = 'button';
    foot.appendChild(prev);
    foot.appendChild(next);
    [head, title, text, task, foot].forEach(function (el) { pop.appendChild(el); });
    close.addEventListener('click', function () { finish(); });
    prev.addEventListener('click', function () { go(-1); });
    next.addEventListener('click', function () {
      if (run && run.index === run.steps.length - 1 && run.def.next) follow(run.def.next);
      else go(1);
    });
    [block, spot, pop].forEach(function (el) { body.appendChild(el); });
    return { block: block, spot: spot, pop: pop, count: count, title: title, text: text, task: task, prev: prev, next: next };
  }

  function place() {
    if (!run || !ui) return;
    var el = run.el;
    if (el && !el.isConnected) {  /* htmx đã thay phần tử: tìm lại theo selector */
      el = run.el = document.querySelector(run.steps[run.index].selector);
    }
    var pop = ui.pop;
    var sheet = window.innerWidth < NARROW;
    pop.classList.toggle('tour-sheet', sheet);
    if (!el || !visible(el)) {
      ui.block.style.clipPath = '';
      ui.spot.hidden = true;
      pop.classList.add('tour-center');
      pop.style.left = pop.style.top = '';
      return;
    }
    pop.classList.remove('tour-center');
    var r = el.getBoundingClientRect();
    var pad = 6;
    ui.spot.hidden = false;
    ui.spot.style.left = (r.left - pad) + 'px';
    ui.spot.style.top = (r.top - pad) + 'px';
    ui.spot.style.width = (r.width + pad * 2) + 'px';
    ui.spot.style.height = (r.height + pad * 2) + 'px';
    /* lớp tối phủ cả màn hình, khoét một lỗ đúng chỗ phần tử (đa giác có lỗ, đi vòng ngoài rồi vòng trong) */
    var x1 = Math.round(r.left - pad), y1 = Math.round(r.top - pad);
    var x2 = Math.round(r.right + pad), y2 = Math.round(r.bottom + pad);
    ui.block.style.clipPath = 'polygon(evenodd, 0 0, 100% 0, 100% 100%, 0 100%, 0 0, ' +
      x1 + 'px ' + y1 + 'px, ' + x1 + 'px ' + y2 + 'px, ' + x2 + 'px ' + y2 + 'px, ' + x2 + 'px ' + y1 + 'px, ' + x1 + 'px ' + y1 + 'px)';
    if (sheet) { pop.style.left = pop.style.top = ''; return; }

    var vw = window.innerWidth, vh = window.innerHeight, gap = 14, edge = 12;
    var pw = pop.offsetWidth, ph = pop.offsetHeight;
    var spots = {
      bottom: { x: r.left + r.width / 2 - pw / 2, y: r.bottom + gap, ok: r.bottom + gap + ph < vh - edge },
      top: { x: r.left + r.width / 2 - pw / 2, y: r.top - gap - ph, ok: r.top - gap - ph > edge },
      right: { x: r.right + gap, y: r.top + r.height / 2 - ph / 2, ok: r.right + gap + pw < vw - edge },
      left: { x: r.left - gap - pw, y: r.top + r.height / 2 - ph / 2, ok: r.left - gap - pw > edge }
    };
    var order = [run.steps[run.index].placement || 'bottom', 'bottom', 'top', 'right', 'left'];
    var pick = null;
    for (var i = 0; i < order.length && !pick; i++) if (spots[order[i]] && spots[order[i]].ok) pick = spots[order[i]];
    if (!pick) pick = { x: vw - pw - edge, y: vh - ph - edge };  /* phần tử to hơn màn hình: hộp ở góc dưới phải */
    pop.style.left = Math.round(Math.max(edge, Math.min(pick.x, vw - pw - edge))) + 'px';
    pop.style.top = Math.round(Math.max(edge, Math.min(pick.y, vh - ph - edge))) + 'px';
  }

  var frame = 0;
  function schedule() {
    if (frame) return;
    frame = window.requestAnimationFrame(function () { frame = 0; place(); });
  }

  function render() {
    var step = run.steps[run.index];
    var last = run.index === run.steps.length - 1;
    ui.count.textContent = 'Bước ' + (run.index + 1) + ' / ' + run.steps.length + (run.def.title ? ' · ' + run.def.title : '');
    ui.title.textContent = step.title || '';
    ui.text.textContent = '';
    (Array.isArray(step.body) ? step.body : [step.body]).forEach(function (para) {
      if (para) ui.text.appendChild(rich(make('p'), para));
    });
    ui.task.textContent = '';
    ui.task.hidden = !step.advanceOn;
    if (step.advanceOn) rich(ui.task, 'Thử ngay: ' + (step.advanceOn.hint || 'làm theo hướng dẫn ở trên') + '.');
    ui.prev.disabled = run.index === 0;
    ui.next.textContent = step.advanceOn ? 'Bỏ qua bước này' : (last ? (run.def.next ? run.def.next.label : 'Xong') : 'Tiếp');
    ui.next.classList.toggle('primary', !step.advanceOn);
    ui.block.classList.toggle('tour-pass', Boolean(step.advanceOn));  /* bước thực hành: bấm được vào trang */
    if (run.el && run.el.scrollIntoView) run.el.scrollIntoView({ block: 'center', inline: 'nearest' });
    place();
    if (step.navOpen) window.setTimeout(schedule, 320);  /* cây phủ trượt vào: tính lại khi đã dừng */
    ui.pop.focus({ preventScroll: true });
  }

  function listen(step) {
    if (run.off) { run.off(); run.off = null; }
    if (!step.advanceOn) return;
    var spec = step.advanceOn;
    var handler = function (e) {
      var hit = e.target && e.target.closest && e.target.closest(spec.selector);
      if (!hit || !run) return;
      var at = run.index;
      window.setTimeout(function () { if (run && run.index === at) go(1); }, spec.event === 'click' ? 150 : 0);
    };
    document.addEventListener(spec.event, handler, true);
    run.off = function () { document.removeEventListener(spec.event, handler, true); };
  }

  function show(index, dir) {
    while (index >= 0 && index < run.steps.length) {
      if (!run.steps[index].navOpen && run.openedNav) body.classList.remove('nav-open');
      var el = target(run.steps[index]);
      if (el !== undefined) {
        run.index = index;
        run.el = el;
        listen(run.steps[index]);
        render();
        return;
      }
      index += dir;
    }
    if (dir > 0) finish(); else show(0, 1);
  }
  function go(dir) { if (run) show(run.index + dir, dir); }

  function follow(next) {
    var href = next.href;
    if (next.hrefFrom) {
      var source = document.querySelector(next.hrefFrom);
      href = source && (source.getAttribute('data-href') || source.getAttribute('href'));
    }
    finish();
    if (!href) return;
    storage('sessionStorage', CONTINUE_KEY, '1');
    window.location.href = href;
  }

  function start() {
    var def = screenTour();
    if (!def) return;
    if (run) finish();
    closeInvite();
    var steps = expand(def, 0).filter(function (step) {  /* bỏ trước những bước chắc chắn không có phần tử */
      return step.later || !step.selector || document.querySelector(step.selector);
    });
    if (!steps.length) return;
    document.querySelectorAll('details.menu[open]').forEach(function (m) { m.removeAttribute('open'); });
    if (!ui) ui = buildUI();
    ui.block.hidden = ui.pop.hidden = false;
    run = { def: def, steps: steps, index: 0, el: null, returnFocus: document.activeElement, off: null,
            openedNav: !body.classList.contains('nav-open') };
    body.classList.add('tour-on');
    show(0, 1);
  }

  function finish() {
    if (!run) return;
    if (run.off) run.off();
    var back = run.returnFocus;
    if (run.openedNav) body.classList.remove('nav-open');
    run = null;
    storage('localStorage', DONE_KEY, '1');
    body.classList.remove('tour-on');
    if (ui) { ui.block.hidden = ui.spot.hidden = ui.pop.hidden = true; }
    if (back && back.isConnected && back.focus) back.focus({ preventScroll: true });
  }

  /* ---- Lời mời xem tour lần đầu ---- */
  var invite = null;
  function closeInvite() { if (invite) { invite.remove(); invite = null; } }
  function offer(def) {
    invite = make('div', 'tour-invite');
    invite.setAttribute('role', 'status');
    var text = make('p', '', 'Lần đầu mở màn hình này? Xem hướng dẫn nhanh: ' + (def.title || 'các vùng chính') + '.');
    var actions = make('div', 'tour-invite-actions');
    var yes = make('button', 'btn primary', 'Xem hướng dẫn');
    var later = make('button', 'btn', 'Để sau');
    yes.type = later.type = 'button';
    yes.addEventListener('click', start);
    later.addEventListener('click', function () { storage('localStorage', DONE_KEY, '1'); closeInvite(); });
    actions.appendChild(yes);
    actions.appendChild(later);
    invite.appendChild(text);
    invite.appendChild(actions);
    body.appendChild(invite);
  }

  /* ---- Phím ---- */
  window.addEventListener('keydown', function (e) {
    if (!run) {
      if (e.key === '?' && !e.ctrlKey && !e.metaKey && !e.altKey && !typing(e.target) && !document.querySelector('dialog[open]')) {
        var dialog = document.getElementById('shortcuts-dialog');
        if (dialog && dialog.showModal) { e.preventDefault(); dialog.showModal(); }
      }
      return;
    }
    if (body.classList.contains('focus-edit') || typing(e.target) || document.querySelector('dialog[open]')) return;
    var step = run.steps[run.index];
    var onSplitter = e.target && e.target.closest && e.target.closest('.splitter');
    if (e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); finish(); return; }
    if (e.key === 'ArrowRight' && !onSplitter) { e.preventDefault(); ui.next.click(); return; }
    if (e.key === 'ArrowLeft' && !onSplitter) { e.preventDefault(); go(-1); return; }
    /* bước chỉ đọc: chặn phím tắt của màn soát để không thao tác nhầm khi đang xem hướng dẫn */
    if (!step.advanceOn && /^[jkaef]$/i.test(e.key) && !e.ctrlKey && !e.metaKey) e.stopImmediatePropagation();
  }, true);

  window.addEventListener('resize', schedule);
  document.addEventListener('scroll', schedule, true);
  document.addEventListener('htmx:afterSettle', schedule);
  /* chế độ sửa tập trung che gần hết giao diện: tạm ẩn tour, hiện lại khi thoát */
  new MutationObserver(function () {
    if (!run || !ui) return;
    var paused = body.classList.contains('focus-edit');
    ui.pop.hidden = ui.block.hidden = paused;
    ui.spot.hidden = paused || !run.el;
    if (!paused) schedule();
  }).observe(body, { attributes: true, attributeFilter: ['class'] });

  document.addEventListener('click', function (e) {
    var btn = e.target.closest && e.target.closest('[data-tour-start]');
    if (btn) { e.preventDefault(); start(); }
  });

  fetch('/static/tours.json?v=1', { credentials: 'same-origin' })
    .then(function (r) { return r.ok ? r.json() : { tours: [] }; })
    .then(function (data) {
      all = data.tours || [];
      var def = screenTour();
      document.querySelectorAll('[data-tour-start]').forEach(function (b) { b.hidden = !def; });
      if (!def) return;
      if (storage('sessionStorage', CONTINUE_KEY)) {
        storage('sessionStorage', CONTINUE_KEY, null);
        start();
      } else if (!storage('localStorage', DONE_KEY) && def.invite !== false) {
        offer(def);
      }
    })
    .catch(function () { /* không tải được nội dung tour: app vẫn dùng bình thường */ });

  window.reviewTour = { start: start, stop: finish };
})();
