/* Khung xem trang PDF gốc (partials/viewer.html): "Vừa trang", phóng to giữ đúng chỗ đang xem, căn vùng tô vàng.
   Các hàm zoomViewer / loadViewer được gọi từ nút trong khung xem nên để ở phạm vi toàn cục. */
var pendingAnchor = null;   /* điểm cần giữ nguyên chỗ sau khi đổi mức phóng to */

function viewerParts() {
  var viewer = document.getElementById('viewer');
  var scroller = viewer && viewer.querySelector('.page-scroll');
  var stack = scroller && scroller.querySelector('.page-stack');
  return stack ? { viewer: viewer, scroller: scroller, stack: stack } : null;
}
function innerSize(el) {
  var cs = getComputedStyle(el);
  return {
    w: el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight),
    h: el.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom)
  };
}

/* "Vừa trang": trang lớn nhất mà cả trang vẫn nằm gọn trong khung, giữ nguyên tỉ lệ trang */
function fitViewer(v) {
  var frame = v.stack.querySelector('.page-frame');
  if (!v.stack.classList.contains('fit') || !frame) return;
  var size = innerSize(v.scroller);
  var aspect = parseFloat(frame.getAttribute('data-aspect')) || 1.414;
  var width = Math.max(Math.floor(Math.min(size.w, (size.h - 2) / aspect)), 120);
  frame.style.width = width + 'px';
  v.stack.classList.toggle('zoomable', width < size.w * 0.95);
  var label = v.viewer.querySelector('.zoom-level');
  if (label) label.textContent = Math.round(width / size.w * 100) + '%';
}

/* Đưa vùng tô vào giữa khung; vùng tô cao hơn khung thì đưa mép trên của nó lên đầu khung */
function centerHighlight(v) {
  var boxes = v.stack.querySelectorAll('.page-highlight');
  if (!boxes.length) return;
  var top = Infinity, bottom = -Infinity, left = Infinity, right = -Infinity;
  boxes.forEach(function (b) {
    var r = b.getBoundingClientRect();
    top = Math.min(top, r.top); bottom = Math.max(bottom, r.bottom);
    left = Math.min(left, r.left); right = Math.max(right, r.right);
  });
  var area = v.scroller.getBoundingClientRect(), h = v.scroller.clientHeight, w = v.scroller.clientWidth, room = 14;
  v.scroller.scrollTop += bottom - top <= h - 2 * room ? (top + bottom) / 2 - (area.top + h / 2) : top - area.top - room;
  v.scroller.scrollLeft += right - left <= w - 2 * room ? (left + right) / 2 - (area.left + w / 2) : left - area.left - room;
}

/* Ghi lại một điểm trên trang (mặc định: giữa khung) để sau khi đổi cỡ vẫn thấy đúng chỗ đó */
function captureAnchor(v, x, y) {
  var area = v.scroller.getBoundingClientRect();
  if (x == null) { x = area.left + v.scroller.clientWidth / 2; y = area.top + v.scroller.clientHeight / 2; }
  var best = null, bestDistance = Infinity;
  v.stack.querySelectorAll('.page-frame').forEach(function (f) {
    var r = f.getBoundingClientRect();
    var d = Math.max(r.left - x, 0, x - r.right) + Math.max(r.top - y, 0, y - r.bottom);
    if (d < bestDistance) { bestDistance = d; best = f; }
  });
  if (!best) return null;
  var r = best.getBoundingClientRect();
  return { page: best.getAttribute('data-page'), fx: (x - r.left) / r.width, fy: (y - r.top) / r.height,
           vx: x - area.left, vy: y - area.top };
}
function restoreAnchor(v, a) {
  var frame = v.stack.querySelector('.page-frame[data-page="' + a.page + '"]') || v.stack.querySelector('.page-frame');
  if (!frame) return;
  var area = v.scroller.getBoundingClientRect(), r = frame.getBoundingClientRect();
  v.scroller.scrollTop += r.top + a.fy * r.height - (area.top + a.vy);
  v.scroller.scrollLeft += r.left + a.fx * r.width - (area.left + a.vx);
}

/* Mỗi khung xem mới (chọn đơn vị, đổi trang, đổi cỡ) chỉ được căn một lần */
function revealHighlight() {
  var v = viewerParts();
  if (!v || v.stack.getAttribute('data-revealed')) return;
  v.stack.setAttribute('data-revealed', '1');
  fitViewer(v);
  var anchor = pendingAnchor;
  pendingAnchor = null;
  if (anchor) restoreAnchor(v, anchor); else centerHighlight(v);
}

function loadViewer(zoom, anchor) {
  var v = viewerParts();
  if (!v) return;
  pendingAnchor = anchor || captureAnchor(v);
  var page = (pendingAnchor && pendingAnchor.page) || v.viewer.getAttribute('data-page');
  var url = v.viewer.getAttribute('data-base') + '?page=' + page + '&zoom=' + zoom;
  var unit = v.viewer.getAttribute('data-unit');
  if (unit) url += '&unit=' + encodeURIComponent(unit);
  htmx.ajax('GET', url, { target: '#viewer', swap: 'outerHTML' });
}
/* Nút −/+: sang mức kế tiếp so với cỡ đang thấy (kể cả khi đang "Vừa trang") */
function zoomViewer(direction) {
  var v = viewerParts(), frame = v && v.stack.querySelector('.page-frame');
  if (!frame) return;
  var current = frame.getBoundingClientRect().width / innerSize(v.scroller).w * 100;
  var levels = v.viewer.getAttribute('data-zooms').split(',').map(Number);
  if (direction < 0) levels.reverse();
  for (var i = 0; i < levels.length; i++) {
    if (direction > 0 ? levels[i] > current + 1 : levels[i] < current - 1) { loadViewer(levels[i]); return; }
  }
}

/* đang "Vừa trang" mà chữ còn nhỏ: bấm vào trang để phóng vừa chiều rộng ngay tại chỗ bấm */
document.addEventListener('click', function (e) {
  var v = viewerParts();
  if (!v || !e.target.closest || !e.target.closest('#viewer .page-stack.fit.zoomable .page-frame')) return;
  loadViewer(100, captureAnchor(v, e.clientX, e.clientY));
});

/* đổi cỡ cửa sổ hoặc kéo thanh chia (shell.js phát sự kiện resize): tính lại "Vừa trang" */
var resizeTimer = null;
window.addEventListener('resize', function () {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(function () {
    var v = viewerParts();
    if (v && v.stack.classList.contains('fit')) { fitViewer(v); centerHighlight(v); }
  }, 120);
});

/* Khung xem mới được chèn (kể cả qua hx-swap-oob khi bấm cây): căn ngay trước khi trình duyệt vẽ */
document.addEventListener('htmx:afterSwap', revealHighlight);
document.addEventListener('htmx:afterSettle', revealHighlight);
document.addEventListener('DOMContentLoaded', revealHighlight);
revealHighlight();
