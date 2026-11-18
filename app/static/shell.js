/* Khung chung: theme, menu, cây cấu trúc dạng lớp phủ (màn hẹp), thanh chia, hộp thoại, bảng, ô lọc.
   Mọi chức năng chính vẫn dùng được khi không có JS (liên kết và form thường); JS chỉ thêm tiện ích. */
(function () {
  'use strict';

  function store(key, value) {
    try {
      if (value === undefined) return localStorage.getItem(key);
      localStorage.setItem(key, value);
    } catch (e) { return null; }
    return null;
  }

  /* ---- Giao diện sáng/tối ---- */
  function updateThemeUI() {
    var dark = document.documentElement.getAttribute('data-theme') === 'dark';
    var label = dark ? 'Chuyển sang giao diện sáng' : 'Chuyển sang giao diện tối';
    document.querySelectorAll('.btn-theme-toggle').forEach(function (btn) {
      btn.setAttribute('title', label);
      btn.setAttribute('aria-label', label);
    });
  }
  window.toggleTheme = function () {
    var next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    store('law_theme', next);
    updateThemeUI();
  };
  if (window.matchMedia) {
    var schemeQuery = window.matchMedia('(prefers-color-scheme: dark)');
    var followScheme = function (e) {  /* chưa tự chọn lần nào thì đi theo hệ điều hành */
      if (store('law_theme')) return;
      document.documentElement.setAttribute('data-theme', e.matches ? 'dark' : 'light');
      updateThemeUI();
    };
    if (schemeQuery.addEventListener) schemeQuery.addEventListener('change', followScheme);
  }

  /* ---- Nhấp chuột chung: con mắt mật khẩu, mở hộp thoại, đóng menu, ngăn điều hướng ---- */
  document.addEventListener('click', function (e) {
    var target = e.target.closest ? e.target : null;
    if (!target) return;

    var eye = target.closest('[data-password-toggle]');
    if (eye) {
      var input = eye.parentNode.querySelector('input');
      if (input) {
        var show = input.type === 'password';
        input.type = show ? 'text' : 'password';
        var label = show ? 'Ẩn mật khẩu' : 'Hiện mật khẩu';
        eye.setAttribute('aria-pressed', show ? 'true' : 'false');
        eye.setAttribute('aria-label', label);
        eye.setAttribute('title', label);
      }
      return;
    }

    var opener = target.closest('[data-open-dialog]');
    if (opener) {
      var dialog = document.getElementById(opener.getAttribute('data-open-dialog'));
      if (dialog && dialog.showModal) { dialog.showModal(); e.preventDefault(); }
      return;
    }
    var closer = target.closest('[data-close-dialog]');
    if (closer) {
      var open = closer.closest('dialog');
      if (open) open.close();
      return;
    }

    if (target.closest('[data-nav-toggle]')) {
      document.body.classList.toggle('nav-open');
      return;
    }
    if (document.body.classList.contains('nav-open') && !target.closest('.navpane, .tour-pop, .tour-block')) {
      document.body.classList.remove('nav-open');
    }

    document.querySelectorAll('details.menu[open]').forEach(function (menu) {
      if (!menu.contains(target)) menu.removeAttribute('open');
    });
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    document.body.classList.remove('nav-open');
    document.querySelectorAll('details.menu[open]').forEach(function (menu) { menu.removeAttribute('open'); });
  });
  /* bấm ra ngoài hộp thoại (vào vùng tối) thì đóng */
  document.addEventListener('click', function (e) {
    if (e.target.tagName === 'DIALOG' && e.target.open) {
      var r = e.target.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) e.target.close();
    }
  });

  /* ---- Thanh chia giữa các ngăn: kéo chuột hoặc phím mũi tên, độ rộng được nhớ ---- */
  var LIMITS = { nav: [180, 480], side: [240, 1400] };
  function applyWidth(workspace, kind, px) {
    var name = kind === 'nav' ? '--nav-width' : '--preview-width';
    workspace.style.setProperty(name, px + 'px');
  }
  function restoreWidths() {
    document.querySelectorAll('.workspace').forEach(function (ws) {
      ['nav', 'side'].forEach(function (kind) {
        var saved = parseInt(store('shell:' + (ws.dataset.layout || 'page') + ':' + kind), 10);
        if (saved) applyWidth(ws, kind, saved);
      });
    });
  }
  function paneWidth(splitter) {
    var kind = splitter.dataset.split;
    var pane = kind === 'nav' ? splitter.previousElementSibling : splitter.nextElementSibling;
    return pane ? pane.getBoundingClientRect().width : 0;
  }
  function setFromSplitter(splitter, px, save) {
    var ws = splitter.closest('.workspace');
    var kind = splitter.dataset.split;
    var max = Math.min(LIMITS[kind][1], ws.getBoundingClientRect().width - 320);
    px = Math.round(Math.max(LIMITS[kind][0], Math.min(px, max)));
    applyWidth(ws, kind, px);
    splitter.setAttribute('aria-valuenow', px);
    if (save) {
      store('shell:' + (ws.dataset.layout || 'page') + ':' + kind, String(px));
      window.dispatchEvent(new Event('resize'));  /* khung PDF tính lại "Vừa trang" */
    }
  }
  document.addEventListener('pointerdown', function (e) {
    var splitter = e.target.closest && e.target.closest('.splitter');
    if (!splitter || e.button !== 0) return;
    e.preventDefault();
    var kind = splitter.dataset.split;
    var startX = e.clientX, startW = paneWidth(splitter);
    splitter.classList.add('dragging');
    document.body.classList.add('resizing');
    splitter.setPointerCapture(e.pointerId);
    function move(ev) {
      var dx = ev.clientX - startX;
      setFromSplitter(splitter, kind === 'nav' ? startW + dx : startW - dx, false);
    }
    function up() {
      splitter.classList.remove('dragging');
      document.body.classList.remove('resizing');
      splitter.removeEventListener('pointermove', move);
      splitter.removeEventListener('pointerup', up);
      splitter.removeEventListener('pointercancel', up);
      setFromSplitter(splitter, paneWidth(splitter), true);
    }
    splitter.addEventListener('pointermove', move);
    splitter.addEventListener('pointerup', up);
    splitter.addEventListener('pointercancel', up);
  });
  document.addEventListener('keydown', function (e) {
    var splitter = e.target.classList && e.target.classList.contains('splitter') ? e.target : null;
    if (!splitter || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight')) return;
    e.preventDefault();
    var step = (e.shiftKey ? 64 : 16) * (e.key === 'ArrowRight' ? 1 : -1);
    setFromSplitter(splitter, paneWidth(splitter) + (splitter.dataset.split === 'nav' ? step : -step), true);
  });

  /* ---- Bảng chi tiết: bấm tiêu đề cột để sắp xếp ---- */
  function cellValue(row, index) {
    var cell = row.cells[index];
    if (!cell) return '';
    var raw = cell.getAttribute('data-sort-value');
    if (raw === null) raw = cell.textContent.trim();
    var num = Number(String(raw).replace(/\./g, '').replace(',', '.'));
    return isNaN(num) || raw === '' ? String(raw).toLowerCase() : num;
  }
  document.addEventListener('click', function (e) {
    var button = e.target.closest && e.target.closest('th .sort');
    if (!button) return;
    var th = button.closest('th');
    var table = th.closest('table');
    var body = table.tBodies[0];
    if (!body) return;
    var index = Array.prototype.indexOf.call(th.parentNode.children, th);
    var ascending = th.getAttribute('aria-sort') !== 'ascending';
    table.querySelectorAll('th[aria-sort]').forEach(function (other) { other.removeAttribute('aria-sort'); });
    th.setAttribute('aria-sort', ascending ? 'ascending' : 'descending');
    var rows = Array.prototype.filter.call(body.rows, function (r) { return !r.hasAttribute('data-pinned'); });
    rows.sort(function (a, b) {
      var x = cellValue(a, index), y = cellValue(b, index);
      var cmp = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), 'vi', { numeric: true });
      return ascending ? cmp : -cmp;
    });
    rows.forEach(function (r) { body.appendChild(r); });
  });

  /* ---- Bảng: cả hàng bấm được (tr[data-href]); liên kết trong ô đầu vẫn dùng được khi không có JS ---- */
  document.addEventListener('click', function (e) {
    var row = e.target.closest && e.target.closest('tr[data-href]');
    if (!row || e.target.closest('a, button, input, select, label, form')) return;
    if (e.ctrlKey || e.metaKey) window.open(row.getAttribute('data-href'), '_blank');
    else window.location.href = row.getAttribute('data-href');
  });

  /* ---- Ô lọc tại chỗ: lọc các mục có data-filter (hoặc cây .tree) trong vùng data-filter-scope ---- */
  function rowState(row) {
    var dot = row.querySelector('.dot');
    var match = dot && /(?:^|\s)st-(\w+)/.exec(dot.className);
    return match ? match[1] : 'unreviewed';
  }
  function rowMatches(row, query, state) {
    /* nội dung đơn vị không hiện trên hàng, nằm trong data-search để vẫn tìm được */
    var text = (row.textContent + ' ' + (row.getAttribute('data-search') || '')).toLowerCase();
    if (query && text.indexOf(query) === -1) return false;
    if (!state || state === 'all') return true;
    if (state === 'uncertain') return row.hasAttribute('data-uncertain');
    return rowState(row) === state;
  }
  function filterTree(scope, query, state) {
    /* cây Điều/Khoản: giữ các hàng khớp và mọi nhánh cha của chúng, mở nhánh khi đang lọc */
    var active = Boolean(query) || (state && state !== 'all');
    var matches = 0;
    scope.querySelectorAll('.node').forEach(function (node) { node.hidden = active; });
    scope.querySelectorAll('.row').forEach(function (row) {
      if (!active || !rowMatches(row, query, state)) return;
      matches += 1;
      var node = row.closest('.node');
      while (node) {
        node.hidden = false;
        if (node.tagName === 'DETAILS' && node !== row.closest('.node')) node.open = true;
        node = node.parentElement && node.parentElement.closest('.node');
      }
    });
    var empty = document.getElementById('tree-empty');
    if (empty) empty.hidden = !active || matches > 0;
    return active ? matches : -1;
  }
  function treeState() {
    var pressed = document.querySelector('.tree-filter[aria-pressed="true"]');
    return pressed ? pressed.getAttribute('data-state') : 'all';
  }
  function treeQuery() {
    var input = document.getElementById('tree-filter');
    return input ? input.value.trim().toLowerCase() : '';
  }
  /* số đơn vị theo trạng thái trên các nút lọc; đếm lại sau mỗi thao tác (chấm trên cây đổi qua hx-swap-oob) */
  function countTree() {
    var tree = document.getElementById('tree');
    if (!tree) return;
    var counts = { all: 0, unreviewed: 0, flagged: 0, structure_error: 0, uncertain: 0 };
    tree.querySelectorAll('.row').forEach(function (row) {
      counts.all += 1;
      var st = rowState(row);
      if (st in counts) counts[st] += 1;
      if (row.hasAttribute('data-uncertain')) counts.uncertain += 1;
    });
    document.querySelectorAll('.tree-filter').forEach(function (btn) {
      var out = btn.querySelector('.num');
      if (out) out.textContent = counts[btn.getAttribute('data-state')] || 0;
    });
  }
  window.refreshTree = function () {
    var tree = document.getElementById('tree');
    if (!tree) return;
    countTree();
    filterTree(tree, treeQuery(), treeState());
  };
  document.addEventListener('click', function (e) {
    var btn = e.target.closest && e.target.closest('.tree-filter');
    if (!btn) return;
    document.querySelectorAll('.tree-filter').forEach(function (b) { b.setAttribute('aria-pressed', b === btn ? 'true' : 'false'); });
    window.refreshTree();
  });

  function filterItems(scope, query) {
    var matches = 0;
    scope.querySelectorAll('[data-filter]').forEach(function (item) {
      var hit = !query || item.getAttribute('data-filter').indexOf(query) !== -1;
      item.hidden = !hit;
      if (hit && query) matches += 1;
    });
    return query ? matches : -1;
  }
  document.addEventListener('input', function (e) {
    var input = e.target;
    if (!input.matches || !input.matches('[data-filter-scope]')) return;
    var query = input.value.trim().toLowerCase();
    var total = 0, filtered = false;
    document.querySelectorAll(input.getAttribute('data-filter-scope')).forEach(function (scope) {
      var n = scope.classList.contains('tree') ? filterTree(scope, query, treeState()) : filterItems(scope, query);
      if (n >= 0) { total += n; filtered = true; }
    });
    var status = document.getElementById(input.getAttribute('data-filter-status') || '');
    if (status) status.textContent = filtered ? total + ' mục khớp' : '';
  });

  /* tệp này nạp ở cuối <body>: các ngăn đã có trong DOM, đặt lại độ rộng trước khi trình duyệt vẽ */
  updateThemeUI();
  restoreWidths();
  countTree();
  document.addEventListener('htmx:afterSwap', updateThemeUI);
})();
