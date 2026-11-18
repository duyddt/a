/* Trang hướng dẫn: dựng các mục từ window.GUIDE (data.js). Không dùng thư viện, chạy cả khi mở trực tiếp tệp (file://).
   Mọi chữ đi vào trang qua textContent; [[A]] thành <kbd>. Lựa chọn vai trò nhớ trong localStorage (guide:role). */
(function () {
  'use strict';
  var G = window.GUIDE;
  if (!G) return;
  var ROLE_KEY = 'guide:role';
  var ROLE_NAMES = {};
  G.roles.forEach(function (r) { ROLE_NAMES[r.id] = r.name; });

  function $(id) { return document.getElementById(id); }
  function make(tag, cls, text) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text !== undefined && text !== null) el.textContent = text;
    return el;
  }
  function rich(el, text) {
    String(text || '').split(/(\[\[[^\]]+\]\])/).forEach(function (part) {
      var m = /^\[\[([^\]]+)\]\]$/.exec(part);
      el.appendChild(m ? make('kbd', '', m[1]) : document.createTextNode(part));
    });
    return el;
  }
  function store(key, value) {
    try {
      if (value === undefined) return localStorage.getItem(key);
      localStorage.setItem(key, value);
    } catch (e) { /* trình duyệt chặn lưu trữ: chỉ không nhớ lựa chọn */ }
    return null;
  }
  function rolesAttr(el, roles) { if (roles && roles.length) el.setAttribute('data-roles', roles.join(' ')); return el; }

  /* ---- Sáng/tối ---- */
  var toggle = document.querySelector('[data-theme-toggle]');
  if (location.protocol === 'file:') {  /* sprite icon ngoài không nạp được qua file://: dùng chữ */
    toggle.textContent = 'Sáng/Tối';
    toggle.classList.add('text-toggle');
  }
  toggle.addEventListener('click', function () {
    var next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    store('law_theme', next);
  });

  /* ---- Vai trò: lọc mọi phần tử có data-roles ---- */
  var currentRole = 'all';
  function applyRole(role) {
    currentRole = ROLE_NAMES[role] ? role : 'all';
    store(ROLE_KEY, currentRole);
    document.querySelectorAll('[data-role-pick]').forEach(function (b) {
      b.setAttribute('aria-checked', b.getAttribute('data-role-pick') === currentRole ? 'true' : 'false');
    });
    document.querySelectorAll('[data-roles]').forEach(function (el) {
      el.hidden = currentRole !== 'all' && el.getAttribute('data-roles').split(' ').indexOf(currentRole) === -1;
    });
    document.querySelectorAll('.role-card').forEach(function (card) {
      card.classList.toggle('picked', card.getAttribute('data-role-id') === currentRole);
    });
    var active = document.querySelector('.screen-tab[aria-selected="true"]');
    if (!active || active.hidden) {
      var first = document.querySelector('.screen-tab:not([hidden])');
      if (first) showScreen(first.getAttribute('data-screen-id'));
    }
    filterFaq();
  }
  document.addEventListener('click', function (e) {
    var pick = e.target.closest && e.target.closest('[data-role-pick]');
    if (pick) applyRole(pick.getAttribute('data-role-pick'));
  });

  var cards = $('role-cards');
  G.roles.forEach(function (role) {
    var card = make('article', 'role-card panel');
    card.setAttribute('data-role-id', role.id);
    card.appendChild(make('h2', '', role.name));
    card.appendChild(make('p', '', role.summary));
    var list = make('ol', 'role-tasks');
    role.tasks.forEach(function (t) { list.appendChild(make('li', '', t)); });
    card.appendChild(list);
    var btn = make('button', 'btn', 'Xem hướng dẫn cho ' + role.name.toLowerCase());
    btn.type = 'button';
    btn.setAttribute('data-role-pick', role.id);
    card.appendChild(btn);
    cards.appendChild(card);
  });

  /* ---- Sơ đồ quy trình ---- */
  var SVG = 'http://www.w3.org/2000/svg';
  function svg(tag, attrs, text) {
    var el = document.createElementNS(SVG, tag);
    Object.keys(attrs || {}).forEach(function (k) { el.setAttribute(k, attrs[k]); });
    if (text) el.textContent = text;
    return el;
  }
  var flow = $('flow-svg');
  var NODE_W = 120, NODE_H = 40;  /* toạ độ hộp trong data.js theo khung 800x250 */
  var byId = {};
  G.workflow.states.forEach(function (s) { byId[s.id] = s; });
  var defs = svg('defs');
  var marker = svg('marker', { id: 'arrow', viewBox: '0 0 10 10', refX: '9', refY: '5', markerWidth: '7', markerHeight: '7', orient: 'auto-start-reverse' });
  marker.appendChild(svg('path', { d: 'M0,0 L10,5 L0,10 z', class: 'flow-arrowhead' }));
  defs.appendChild(marker);
  flow.appendChild(defs);
  function edgePoint(s, toward) {  /* điểm trên viền hộp, hướng về tâm hộp kia */
    var cx = s.x + NODE_W / 2, cy = s.y + NODE_H / 2;
    var dx = toward.x + NODE_W / 2 - cx, dy = toward.y + NODE_H / 2 - cy;
    var scale = Math.min(Math.abs((NODE_W / 2) / (dx || 1e-6)), Math.abs((NODE_H / 2) / (dy || 1e-6)));
    return { x: cx + dx * scale, y: cy + dy * scale };
  }
  G.workflow.edges.forEach(function (e) {
    var a = byId[e.from], b = byId[e.to], d, lx, ly;
    if (e.curve) {  /* Mở lại: vòng phía trên từ Đã phê duyệt về Đang chỉnh sửa */
      var x1 = a.x + NODE_W / 2, x2 = b.x + NODE_W / 2, y = a.y;
      d = 'M' + x1 + ',' + y + ' C' + x1 + ',' + (y - 60) + ' ' + x2 + ',' + (y - 60) + ' ' + x2 + ',' + y;
      lx = (x1 + x2) / 2; ly = y - 50;
    } else {
      var p = edgePoint(a, b), q = edgePoint(b, a);
      /* Trả lại và Nộp lại nối cùng hai hộp: tách thành hai đường song song, nhãn ở hai bên */
      var offset = e.to === 'returned' ? 22 : (e.from === 'returned' ? -22 : 0);
      d = 'M' + (p.x + offset) + ',' + p.y + ' L' + (q.x + offset) + ',' + q.y;
      lx = (p.x + q.x) / 2 + offset * 2.4; ly = (p.y + q.y) / 2 + (offset ? 4 : -8);
    }
    flow.appendChild(svg('path', { d: d, class: 'flow-edge', 'marker-end': 'url(#arrow)' }));
    flow.appendChild(svg('text', { x: lx, y: ly, class: 'flow-edge-label', 'text-anchor': 'middle' }, e.label));
  });
  G.workflow.states.forEach(function (s) {
    var g = svg('g', { class: 'flow-node wf-' + s.id, tabindex: '0', role: 'button', 'data-state': s.id, 'aria-label': s.label });
    g.appendChild(svg('rect', { x: s.x, y: s.y, width: NODE_W, height: NODE_H }));
    g.appendChild(svg('text', { x: s.x + NODE_W / 2, y: s.y + NODE_H / 2 + 5, 'text-anchor': 'middle' }, s.label));
    flow.appendChild(g);
  });
  function showState(id) {
    var s = byId[id];
    flow.querySelectorAll('.flow-node').forEach(function (n) {
      var on = n.getAttribute('data-state') === id;
      n.classList.toggle('selected', on);
      n.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    var box = $('flow-detail');
    box.textContent = '';
    box.appendChild(make('h3', 'wf-status wf-' + s.id, s.label));
    var dl = make('dl', 'flow-facts');
    [['Ai làm', s.who], ['Ở bước này', s.what], ['Bước tiếp theo', s.next]].forEach(function (row) {
      dl.appendChild(make('dt', '', row[0]));
      dl.appendChild(make('dd', '', row[1]));
    });
    box.appendChild(dl);
  }
  flow.addEventListener('click', function (e) {
    var node = e.target.closest('.flow-node');
    if (node) showState(node.getAttribute('data-state'));
  });
  flow.addEventListener('keydown', function (e) {
    var node = e.target.closest && e.target.closest('.flow-node');
    if (node && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); showState(node.getAttribute('data-state')); }
  });
  showState('editing');
  var unitStates = $('unit-states');
  G.workflow.unitStates.forEach(function (u) {
    var li = make('li');
    li.appendChild(make('span', 'dot ' + u.cls));
    li.appendChild(make('strong', '', u.label));
    li.appendChild(document.createTextNode(' ' + u.text));
    unitStates.appendChild(li);
  });

  /* ---- Từng màn hình: ảnh + điểm nóng ---- */
  var tabs = $('screen-tabs'), view = $('screen-view');
  var screens = {};
  G.screens.forEach(function (sc) {
    screens[sc.id] = sc;
    var tab = rolesAttr(make('button', 'screen-tab', sc.title), sc.roles);
    tab.type = 'button';
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-selected', 'false');
    tab.setAttribute('aria-controls', 'screen-view');
    tab.setAttribute('data-screen-id', sc.id);
    tabs.appendChild(tab);
  });
  tabs.addEventListener('click', function (e) {
    var tab = e.target.closest('.screen-tab');
    if (tab) showScreen(tab.getAttribute('data-screen-id'));
  });
  tabs.addEventListener('keydown', function (e) {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    var list = Array.prototype.filter.call(tabs.querySelectorAll('.screen-tab'), function (t) { return !t.hidden; });
    var i = list.indexOf(document.activeElement);
    if (i < 0) return;
    var next = list[(i + (e.key === 'ArrowRight' ? 1 : list.length - 1)) % list.length];
    next.focus();
    showScreen(next.getAttribute('data-screen-id'));
  });
  function pickSpot(index, fromList) {
    view.querySelectorAll('[data-spot]').forEach(function (el) {
      var on = Number(el.getAttribute('data-spot')) === index;
      el.classList.toggle('active', on);
      if (el.tagName === 'BUTTON') el.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    var item = view.querySelector('li[data-spot="' + index + '"]');
    var list = item && item.parentNode;
    if (!item || fromList) return;
    if (list.scrollHeight > list.clientHeight) list.scrollTop = item.offsetTop - 8;  /* danh sách cuộn riêng (màn rộng) */
    else if (window.innerWidth < 1100 && item.scrollIntoView) item.scrollIntoView({ block: 'nearest' });
  }
  function showScreen(id) {
    var sc = screens[id];
    if (!sc) return;
    tabs.querySelectorAll('.screen-tab').forEach(function (t) {
      var on = t.getAttribute('data-screen-id') === id;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
    });
    view.textContent = '';
    view.appendChild(rich(make('p', 'screen-lead'), sc.lead));
    var wrap = make('div', 'screen-wrap');
    var figure = make('figure', 'shot');
    var img = make('img');
    img.src = sc.img;
    img.alt = 'Ảnh chụp màn hình ' + sc.title;
    img.width = 1440;
    img.height = 900;
    img.loading = 'lazy';
    figure.appendChild(img);
    var list = make('ol', 'spot-list');
    sc.spots.forEach(function (sp, i) {
      var n = i + 1;
      var box = rolesAttr(make('div', 'spot-box'), sp.roles);
      box.setAttribute('data-spot', n);
      box.style.left = sp.x + '%';
      box.style.top = sp.y + '%';
      box.style.width = sp.w + '%';
      box.style.height = sp.h + '%';
      figure.appendChild(box);
      var pin = rolesAttr(make('button', 'spot-pin', String(n)), sp.roles);
      pin.type = 'button';
      pin.setAttribute('data-spot', n);
      pin.setAttribute('aria-label', n + '. ' + sp.title);
      pin.setAttribute('aria-pressed', 'false');
      pin.style.left = Math.min(sp.x + sp.w, 98) + '%';
      pin.style.top = Math.max(sp.y, 1.5) + '%';
      figure.appendChild(pin);
      var li = rolesAttr(make('li'), sp.roles);
      li.setAttribute('data-spot', n);
      var head = make('button', 'spot-item');
      head.type = 'button';
      head.appendChild(make('span', 'spot-num', String(n)));
      head.appendChild(make('strong', '', sp.title));
      li.appendChild(head);
      li.appendChild(rich(make('p', 'spot-text'), sp.text));
      list.appendChild(li);
    });
    figure.addEventListener('click', function (e) {
      var pin = e.target.closest('.spot-pin');
      if (pin) pickSpot(Number(pin.getAttribute('data-spot')));
    });
    list.addEventListener('click', function (e) {
      var li = e.target.closest('li[data-spot]');
      if (li) pickSpot(Number(li.getAttribute('data-spot')));
    });
    list.addEventListener('mouseover', function (e) {
      var li = e.target.closest('li[data-spot]');
      if (li && !li.classList.contains('active')) pickSpot(Number(li.getAttribute('data-spot')), true);
    });
    wrap.appendChild(figure);
    wrap.appendChild(list);
    view.appendChild(wrap);
    applyRoleTo(view);
  }
  function applyRoleTo(root) {
    root.querySelectorAll('[data-roles]').forEach(function (el) {
      el.hidden = currentRole !== 'all' && el.getAttribute('data-roles').split(' ').indexOf(currentRole) === -1;
    });
  }

  /* ---- Phím tắt ---- */
  var table = $('keys-table');
  var lastGroup = null, tbody = null;
  G.shortcuts.forEach(function (k, i) {
    if (k.group !== lastGroup) {
      tbody = make('tbody');
      var hr = make('tr', 'group-row');
      var th = make('th', '', k.group);
      th.colSpan = 2;
      th.scope = 'rowgroup';
      hr.appendChild(th);
      tbody.appendChild(hr);
      table.appendChild(tbody);
      lastGroup = k.group;
    }
    var tr = make('tr');
    tr.setAttribute('data-key-row', i);
    var keys = make('td');
    k.keys.forEach(function (key, j) {
      if (j) keys.appendChild(document.createTextNode(' + '));
      keys.appendChild(make('kbd', '', key));
    });
    tr.appendChild(keys);
    tr.appendChild(make('td', '', k.text));
    tbody.appendChild(tr);
  });
  function shortcutIndex(e) {
    var key = e.key;
    for (var i = 0; i < G.shortcuts.length; i++) {
      var keys = G.shortcuts[i].keys;
      if (keys.length === 2 && keys[0] === 'Ctrl' && key === 'Enter' && (e.ctrlKey || e.metaKey)) return i;
      if (keys.length === 1 && keys[0].toLowerCase() === String(key).toLowerCase() && !e.ctrlKey) return i;
      if (keys[0] === '←' && (key === 'ArrowLeft' || key === 'ArrowRight')) return i;
      if (keys[0] === 'Esc' && key === 'Escape') return i;
    }
    return -1;
  }
  $('key-try').addEventListener('keydown', function (e) {
    if (e.key === 'Tab' || e.key === 'Shift') return;
    var i = shortcutIndex(e);
    var out = $('key-try-out');
    table.querySelectorAll('tr.hit').forEach(function (r) { r.classList.remove('hit'); });
    if (i < 0) { out.textContent = 'Phím ' + (e.key === ' ' ? 'cách' : e.key) + ' không có chức năng trong app.'; return; }
    e.preventDefault();
    var row = table.querySelector('[data-key-row="' + i + '"]');
    row.classList.add('hit');
    out.textContent = '';
    G.shortcuts[i].keys.forEach(function (key, j) {
      if (j) out.appendChild(document.createTextNode(' + '));
      out.appendChild(make('kbd', '', key));
    });
    out.appendChild(document.createTextNode(' ' + G.shortcuts[i].text + ' (' + G.shortcuts[i].group.toLowerCase() + ').'));
  });

  /* ---- Bài tập thực hành ---- */
  var ACTIONS = {
    approve: { label: 'Duyệt & tiếp', key: 'A', cls: 'approve' },
    edit: { label: 'Sửa chữ', key: 'E', cls: 'edit-toggle' },
    flag: { label: 'Gắn cờ', key: 'F', cls: 'flag' },
    structure_error: { label: 'Lỗi cấu trúc', key: null, cls: 'structure' }
  };
  var practice = $('practice-app');
  var state = { index: 0, score: 0, results: [] };
  function norm(text) { return String(text || '').normalize('NFC').replace(/\s+/g, ' ').trim(); }

  function renderExercise() {
    var ex = G.exercises[state.index];
    practice.textContent = '';
    practice.onkeydown = null;
    if (!ex) return renderSummary();
    var head = make('div', 'practice-head');
    head.appendChild(make('span', 'muted small', 'Bài ' + (state.index + 1) + ' / ' + G.exercises.length + ' · Đúng ' + state.score));
    var meter = make('span', 'meter practice-meter');
    var fill = make('span', 'meter-fill');
    fill.style.width = Math.round(state.index / G.exercises.length * 100) + '%';
    meter.appendChild(fill);
    head.appendChild(meter);
    practice.appendChild(head);

    var desk = make('div', 'practice-desk');
    var left = make('div', 'practice-text');
    left.appendChild(make('h3', 'unit-heading', ex.unit));
    left.appendChild(make('p', 'muted small', 'Chữ OCR'));
    var ocr = make('pre', 'content practice-ocr', ex.ocr);
    left.appendChild(ocr);
    var right = make('figure', 'practice-pdf');
    var img = make('img');
    img.src = ex.img;
    img.alt = 'Ảnh vùng PDF gốc của ' + ex.unit;
    right.appendChild(img);
    right.appendChild(make('figcaption', 'muted small', 'Bản PDF gốc'));
    desk.appendChild(left);
    desk.appendChild(right);
    practice.appendChild(desk);

    var area = make('div', 'practice-input');
    practice.appendChild(area);
    var row = make('div', 'action-row practice-actions');
    row.setAttribute('role', 'toolbar');
    row.setAttribute('aria-label', 'Chọn thao tác');
    Object.keys(ACTIONS).forEach(function (id) {
      var a = ACTIONS[id];
      var b = make('button', 'ribbon-btn ' + a.cls);
      b.type = 'button';
      b.setAttribute('data-act', id);
      b.appendChild(document.createTextNode(a.label));
      if (a.key) { b.appendChild(document.createTextNode(' ')); b.appendChild(make('kbd', '', a.key)); }
      row.appendChild(b);
    });
    practice.appendChild(row);
    var feedback = make('div', 'practice-feedback');
    feedback.setAttribute('aria-live', 'polite');
    practice.appendChild(feedback);

    function openEdit() {
      area.textContent = '';
      var label = make('label', 'field', 'Sửa cho khớp bản PDF');
      var ta = make('textarea', 'input practice-edit');
      ta.value = ex.ocr;
      ta.rows = 4;
      ta.spellcheck = false;
      label.appendChild(ta);
      area.appendChild(label);
      var bar = make('div', 'practice-bar');
      var save = make('button', 'btn primary', 'Lưu');
      save.type = 'button';
      var hint = make('span', 'muted small');
      rich(hint, '[[Ctrl]] + [[Enter]] để lưu, [[Esc]] để hủy');
      var cancel = make('button', 'btn', 'Hủy');
      cancel.type = 'button';
      bar.appendChild(save); bar.appendChild(cancel); bar.appendChild(hint);
      area.appendChild(bar);
      save.addEventListener('click', function () { grade('edit', ta.value); });
      cancel.addEventListener('click', function () { area.textContent = ''; });
      ta.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); grade('edit', ta.value); }
        if (e.key === 'Escape') { e.preventDefault(); area.textContent = ''; row.querySelector('[data-act="edit"]').focus(); }
      });
      ta.focus();
    }
    function openReason(kind) {
      area.textContent = '';
      var label = make('label', 'field', kind === 'flag' ? 'Lý do gắn cờ' : 'Lỗi cấu trúc ở đâu');
      var input = make('input', 'input');
      input.type = 'text';
      input.placeholder = 'Bắt buộc: lý do sai hoặc vị trí lỗi';
      label.appendChild(input);
      area.appendChild(label);
      var bar = make('div', 'practice-bar');
      var ok = make('button', 'btn primary', kind === 'flag' ? 'Gắn cờ' : 'Báo lỗi cấu trúc');
      ok.type = 'button';
      var cancel = make('button', 'btn', 'Hủy');
      cancel.type = 'button';
      var err = make('span', 'practice-err small');
      bar.appendChild(ok); bar.appendChild(cancel); bar.appendChild(err);
      area.appendChild(bar);
      function submit() {
        if (norm(input.value).length < 5) { err.textContent = 'Cần ghi lý do cụ thể, như trong app.'; input.focus(); return; }
        grade(kind, input.value);
      }
      ok.addEventListener('click', submit);
      cancel.addEventListener('click', function () { area.textContent = ''; });
      input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); submit(); }
        if (e.key === 'Escape') { e.preventDefault(); area.textContent = ''; }
      });
      input.focus();
    }
    function choose(act) {
      if (practice.classList.contains('answered')) return;
      if (act === 'approve') grade('approve');
      else if (act === 'edit') openEdit();
      else openReason(act);
    }
    row.addEventListener('click', function (e) {
      var b = e.target.closest('[data-act]');
      if (b) choose(b.getAttribute('data-act'));
    });
    practice.onkeydown = function (e) {
      if (e.ctrlKey || e.metaKey || e.altKey || /^(INPUT|TEXTAREA)$/.test(e.target.tagName)) return;
      var map = { a: 'approve', e: 'edit', f: 'flag' };
      var act = map[String(e.key).toLowerCase()];
      if (act && !practice.classList.contains('answered')) { e.preventDefault(); choose(act); }
    };

    function grade(act, value) {
      var right = act === ex.answer;
      var detail = '';
      if (right && act === 'edit') {
        right = norm(value) === norm(ex.correct);
        if (!right) detail = norm(value) === norm(ex.ocr) ? 'Bạn chưa sửa chữ nào.' : 'Đối chiếu lại với các chỗ sai bên dưới.';
      }
      practice.classList.add('answered');
      row.querySelectorAll('button').forEach(function (b) { b.disabled = true; });
      area.textContent = '';
      state.results.push({ unit: ex.unit, right: right });
      if (right) state.score += 1;
      feedback.textContent = '';
      feedback.className = 'practice-feedback alert ' + (right ? 'success' : 'error');
      var body = make('div');
      var verdict = right ? 'Đúng.' : (act === ex.answer ? 'Chọn Sửa chữ là đúng, nhưng chữ chưa khớp bản PDF.'
        : 'Chưa đúng. Đáp án: ' + ACTIONS[ex.answer].label + '.');
      body.appendChild(make('strong', '', verdict));
      if (detail) body.appendChild(make('p', '', detail));
      body.appendChild(make('p', '', ex.explain));
      if (ex.fixes) {
        var fx = make('ul', 'fix-list');
        ex.fixes.forEach(function (f) {
          var li = make('li');
          li.appendChild(make('del', '', f[0]));
          li.appendChild(document.createTextNode(' thành '));
          li.appendChild(make('ins', '', f[1]));
          fx.appendChild(li);
        });
        body.appendChild(fx);
      }
      var next = make('button', 'btn primary', state.index + 1 < G.exercises.length ? 'Bài tiếp theo' : 'Xem kết quả');
      next.type = 'button';
      next.addEventListener('click', function () {
        state.index += 1;
        practice.classList.remove('answered');
        renderExercise();
        practice.focus();
      });
      body.appendChild(next);
      feedback.appendChild(body);
      next.focus();
    }
  }
  function renderSummary() {
    var box = make('div', 'practice-summary');
    box.appendChild(make('h3', '', 'Kết quả: ' + state.score + ' / ' + G.exercises.length + ' bài đúng'));
    var list = make('ol', 'summary-list');
    state.results.forEach(function (r) {
      var li = make('li', r.right ? 'ok' : 'miss');
      li.appendChild(make('span', 'dot ' + (r.right ? 'st-approved' : 'st-flagged')));
      li.appendChild(document.createTextNode(r.unit + ': ' + (r.right ? 'đúng' : 'chưa đúng')));
      list.appendChild(li);
    });
    box.appendChild(list);
    box.appendChild(make('p', '', 'Nhớ: so từng chữ số; đọc theo nghĩa để bắt chữ bị rơi; tách sai thì báo Lỗi cấu trúc thay vì xóa chữ; không đọc rõ thì Gắn cờ, không đoán.'));
    var again = make('button', 'btn primary', 'Làm lại');
    again.type = 'button';
    again.addEventListener('click', function () { state = { index: 0, score: 0, results: [] }; renderExercise(); });
    box.appendChild(again);
    practice.appendChild(box);
  }
  practice.tabIndex = -1;
  renderExercise();

  /* ---- Quản trị ---- */
  var adminCards = $('admin-cards');
  G.admin.forEach(function (task, i) {
    var card = make('article', 'admin-card panel');
    card.appendChild(make('h3', '', (i + 1) + '. ' + task.title));
    var ol = make('ol');
    task.steps.forEach(function (s) { ol.appendChild(make('li', '', s)); });
    card.appendChild(ol);
    if (task.screen && screens[task.screen]) {
      var link = make('a', '', 'Xem màn hình ' + screens[task.screen].title);
      link.href = '#screens';
      link.addEventListener('click', function () { showScreen(task.screen); });
      card.appendChild(link);
    }
    adminCards.appendChild(card);
  });

  /* ---- FAQ ---- */
  var faqList = $('faq-list');
  G.faq.forEach(function (item) {
    var d = rolesAttr(make('details', 'faq-item'), item.roles);
    d.appendChild(make('summary', '', item.q));
    d.appendChild(rich(make('p'), item.a));
    d.setAttribute('data-text', (item.q + ' ' + item.a).toLowerCase());
    faqList.appendChild(d);
  });
  function filterFaq() {
    var q = norm($('faq-filter').value).toLowerCase();
    var shown = 0;
    faqList.querySelectorAll('.faq-item').forEach(function (d) {
      var roles = d.getAttribute('data-roles');
      var roleOk = currentRole === 'all' || !roles || roles.split(' ').indexOf(currentRole) !== -1;
      var hit = roleOk && (!q || d.getAttribute('data-text').indexOf(q) !== -1);
      d.hidden = !hit;
      if (hit) shown += 1;
      if (q && hit) d.open = true;
    });
    $('faq-status').textContent = q ? shown + ' câu khớp' : '';
  }
  $('faq-filter').addEventListener('input', filterFaq);

  /* ---- Khởi tạo ---- */
  var params = new URLSearchParams(location.search);
  showScreen(params.get('screen') || 'review-editor');
  applyRole(params.get('role') || store(ROLE_KEY) || 'all');
})();
