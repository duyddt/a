/* Nội dung trang hướng dẫn (index.html đọc qua window.GUIDE). Chỉ là dữ liệu, không có HTML:
   [[A]] trong chữ hiện thành phím. roles: các vai trò thấy mục đó (reviewer, approver, admin); bỏ trống là mọi vai trò.
   Toạ độ điểm nóng (x, y, w, h) tính theo % ảnh chụp 1440x900 trong img/; chụp lại ảnh thì đo lại (README.md). */
window.GUIDE = {
  roles: [
    {
      id: 'reviewer',
      name: 'Người chỉnh sửa',
      summary: 'So chữ OCR của từng đơn vị với bản PDF gốc, sửa chỗ sai, duyệt, rồi bấm Hoàn thành văn bản.',
      tasks: ['Mở văn bản được giao ở Việc của tôi', 'Soát từng đơn vị: Duyệt, Sửa chữ, Gắn cờ hoặc Lỗi cấu trúc', 'Bấm Hoàn thành văn bản khi đã soát hết', 'Sửa tiếp nếu người phê duyệt trả lại']
    },
    {
      id: 'approver',
      name: 'Người phê duyệt',
      summary: 'So các bản của người chỉnh sửa, chọn bản đúng làm bản chốt, duyệt từng đơn vị, rồi hoàn tất hoặc trả lại.',
      tasks: ['Mở văn bản ở Chờ tôi phê duyệt', 'So sánh các bản, bấm Chọn bản này cho bản đúng', 'Duyệt từng đơn vị trên bản chốt', 'Hoàn tất phê duyệt, hoặc Trả lại kèm ghi chú']
    },
    {
      id: 'admin',
      name: 'Admin',
      summary: 'Cấp tài khoản, nhập văn bản, giao việc, theo dõi tiến độ và mở lại văn bản khi cần.',
      tasks: ['Tạo tài khoản và chọn vai trò', 'Tải văn bản lên đúng chủ đề', 'Phân công người chỉnh sửa và người phê duyệt', 'Theo dõi Tổng quan và Thống kê, mở lại văn bản khi cần']
    }
  ],

  /* Quy trình của một văn bản: services/workflow.py */
  workflow: {
    states: [
      { id: 'unassigned', label: 'Chưa giao', x: 10, y: 70,
        who: 'Admin',
        what: 'Văn bản vừa nhập, chưa có ai được giao.',
        next: 'Admin vào Phân công, đánh dấu văn bản, chọn người chỉnh sửa và người phê duyệt, bấm Giao văn bản đã đánh dấu.' },
      { id: 'editing', label: 'Đang chỉnh sửa', x: 230, y: 70,
        who: 'Người chỉnh sửa',
        what: 'Mỗi người chỉnh sửa soát trên bản riêng của mình. Người khác không thấy và không sửa được bản đó.',
        next: 'Soát hết các đơn vị rồi bấm Hoàn thành văn bản. Khi mọi người được giao đã bấm, văn bản chuyển sang Chờ phê duyệt.' },
      { id: 'pending', label: 'Chờ phê duyệt', x: 450, y: 70,
        who: 'Người phê duyệt',
        what: 'Người phê duyệt xem So sánh các bản, chọn bản đúng làm bản chốt và duyệt từng đơn vị. Người phê duyệt không sửa chữ.',
        next: 'Hoàn tất phê duyệt khi mọi đơn vị trên bản chốt đã duyệt. Còn sai thì Trả lại kèm ghi chú.' },
      { id: 'approved', label: 'Đã phê duyệt', x: 670, y: 70,
        who: 'Admin',
        what: 'Văn bản đã xong. Xuất JSON đã duyệt ở menu Khác lấy bản chốt.',
        next: 'Cần sửa lại thì Admin bấm Mở lại.' },
      { id: 'returned', label: 'Bị trả lại', x: 450, y: 190,
        who: 'Người chỉnh sửa',
        what: 'Người phê duyệt đã trả lại với ghi chú chỗ còn sai. Ghi chú hiện trên đầu màn soát và trong Việc của tôi.',
        next: 'Sửa theo ghi chú rồi bấm Hoàn thành văn bản lần nữa.' }
    ],
    edges: [
      { from: 'unassigned', to: 'editing', label: 'Giao việc' },
      { from: 'editing', to: 'pending', label: 'Tất cả nộp' },
      { from: 'pending', to: 'approved', label: 'Hoàn tất' },
      { from: 'pending', to: 'returned', label: 'Trả lại' },
      { from: 'returned', to: 'pending', label: 'Nộp lại' },
      { from: 'approved', to: 'editing', label: 'Admin mở lại', curve: true }
    ],
    unitStates: [
      { cls: 'st-unreviewed', label: 'Chưa soát', text: 'Chưa ai thao tác trên đơn vị trong bản này.' },
      { cls: 'st-approved', label: 'Đã duyệt', text: 'Chữ khớp bản PDF (sau khi đã sửa nếu cần).' },
      { cls: 'st-flagged', label: 'Gắn cờ', text: 'Cần hỏi lại: chữ trên PDF không đọc rõ, nội dung đáng ngờ. Bắt buộc ghi lý do.' },
      { cls: 'st-structure_error', label: 'Lỗi cấu trúc', text: 'Đơn vị tách sai: gộp hai khoản, cắt đôi một điểm, sai số hiệu. Bắt buộc ghi chỗ sai.' }
    ]
  },

  screens: [
    {
      id: 'login', title: 'Đăng nhập', img: 'img/login.webp',
      lead: 'Không có đăng ký tự do. Tài khoản do Admin cấp.',
      spots: [
        { x: 38.1, y: 30.8, w: 23.8, h: 3.6, title: 'Tên tài khoản', text: 'Tên Admin đã cấp, chữ thường, không dấu.' },
        { x: 38.1, y: 38.4, w: 23.8, h: 3.6, title: 'Mật khẩu', text: 'Biểu tượng con mắt ở cuối ô hiện hoặc ẩn mật khẩu. Sai 10 lần trong 15 phút thì tài khoản bị tạm chặn đăng nhập.' },
        { x: 38.1, y: 44.0, w: 23.8, h: 4.0, title: 'Đăng nhập', text: 'Vào trang Việc của tôi, Việc phê duyệt hoặc Tổng quan tùy vai trò.' },
        { x: 38.1, y: 52.6, w: 23.8, h: 2.4, title: 'Hướng dẫn sử dụng', text: 'Mở trang này. Quên mật khẩu: liên hệ Admin để đặt lại.' }
      ]
    },
    {
      id: 'home-reviewer', title: 'Việc của tôi', img: 'img/home-reviewer.webp', roles: ['reviewer'],
      lead: 'Trang đầu tiên của người chỉnh sửa: các văn bản được giao, xếp theo việc cần làm.',
      spots: [
        { x: 16.5, y: 0, w: 11.9, h: 5.7, title: 'Các mục chính', text: 'Việc của tôi và Văn bản (kho toàn bộ văn bản).' },
        { x: 76.1, y: 1.1, w: 9.0, h: 3.6, title: 'Menu Hướng dẫn', text: 'Xem hướng dẫn của màn hình đang mở, bảng phím tắt, hoặc trang này.' },
        { x: 6.7, y: 14.0, w: 86.7, h: 14.6, title: 'Tiếp tục', text: 'Văn bản và đơn vị bạn soát gần nhất. Bấm Tiếp tục để quay lại đúng chỗ.' },
        { x: 6.7, y: 31.7, w: 86.7, h: 12.8, title: 'Đang làm', text: 'Văn bản được giao mà bạn chưa bấm Hoàn thành. Thanh tiến độ cho biết đã duyệt bao nhiêu đơn vị.' },
        { x: 6.7, y: 47.7, w: 86.7, h: 12.8, title: 'Đã nộp, chờ phê duyệt', text: 'Văn bản bạn đã bấm Hoàn thành. Văn bản bị trả lại hiện ở mục riêng phía trên Đang làm.' }
      ]
    },
    {
      id: 'review-editor', title: 'Màn soát văn bản', img: 'img/review-editor.webp', roles: ['reviewer', 'approver', 'admin'],
      lead: 'Ba khung: cây cấu trúc, đơn vị đang soát, trang PDF gốc. Phần lớn thời gian làm việc ở đây.',
      spots: [
        { x: 1.1, y: 9.4, w: 79.7, h: 5.0, title: 'Thanh văn bản', text: 'Số hiệu, tên, trạng thái quy trình, người chỉnh sửa (mỗi người một màu, dấu ✓ khi đã nộp) và người phê duyệt.' },
        { x: 7.3, y: 12.5, w: 16.2, h: 1.9, title: 'Tiến độ', text: 'Số đơn vị đã duyệt trên tổng số trong bản của bạn.' },
        { x: 81.9, y: 10.1, w: 16.9, h: 3.6, title: 'Nút của cả văn bản', text: 'Hoàn thành văn bản (người chỉnh sửa). Menu Khác: độ chính xác OCR, xuất JSON.' },
        { x: 0.7, y: 21.1, w: 16.7, h: 10.0, title: 'Tìm và lọc trên cây', text: 'Ô tìm lọc theo chữ trong nội dung. Nút lọc chỉ hiện đơn vị chưa soát, gắn cờ, lỗi cấu trúc hoặc không chắc.' },
        { x: 2.6, y: 55.8, w: 14.1, h: 3.3, title: 'Đơn vị đang mở', text: 'Hàng được tô trên cây. Chấm màu bên trái là trạng thái; vạch màu bên phải cho biết người nào đã có bản riêng của đơn vị này.' },
        { x: 18.4, y: 15.6, w: 43.3, h: 25.0, title: 'Đơn vị đang soát', text: 'Chữ OCR có đánh số dòng. Nhãn Bản của bạn: thay đổi chỉ nằm trong bản riêng của bạn. A− và A+ đổi cỡ chữ.' },
        { x: 67.3, y: 72.8, w: 26.5, h: 4.1, title: 'Vùng tô vàng', text: 'Vị trí của đơn vị trên trang PDF. Khung nét đứt là vị trí ước lượng (bản scan không có lớp chữ). Bấm vào trang để phóng to đúng chỗ.' },
        { x: 62.0, y: 20.0, w: 38.0, h: 4.5, title: 'Thanh công cụ trang', text: 'Sang trang, gõ số trang, phóng to, thu nhỏ, Vừa trang hoặc Chiều rộng.' },
        { x: 22.2, y: 95.3, w: 23.0, h: 3.6, title: 'Nút thẩm định', text: '[[A]] Duyệt & tiếp, [[E]] Sửa chữ, [[F]] Gắn cờ, Lỗi cấu trúc. Màu ô đầu nút là trạng thái mà nút tạo ra.' },
        { x: 58.2, y: 95.3, w: 2.3, h: 3.6, title: 'Trước, Tiếp', text: '[[K]] về đơn vị trước, [[J]] sang đơn vị kế tiếp. Chuyển đơn vị không thay đổi dữ liệu.' },
        { x: 0, y: 93.8, w: 18.1, h: 6.2, title: 'Chú thích', text: 'Ý nghĩa các màu trạng thái trên cây.' }
      ]
    },
    {
      id: 'review-focus-edit', title: 'Sửa chữ', img: 'img/review-focus-edit.webp', roles: ['reviewer', 'admin'],
      lead: 'Nhấn [[E]] trên màn soát: chỉ còn trang PDF và ô sửa chữ của đơn vị.',
      spots: [
        { x: 1.1, y: 11.9, w: 59.4, h: 83.8, title: 'Ô sửa chữ', text: 'Sửa trực tiếp cho khớp bản PDF. Không thêm, bớt số hiệu điểm, khoản: sai tách đơn vị thì báo Lỗi cấu trúc.' },
        { x: 25.1, y: 2.3, w: 17.2, h: 3.6, title: 'Loại lỗi OCR', text: 'Không bắt buộc. Giúp thống kê loại lỗi OCR hay gặp (sai dấu, sai số, thiếu chữ...).' },
        { x: 47.7, y: 2.3, w: 12.1, h: 3.6, title: 'Lưu', text: '[[Ctrl]] + [[Enter]] để lưu, [[Esc]] để hủy. Lưu xong đơn vị vẫn ở trạng thái cũ: phải Duyệt.' },
        { x: 62.0, y: 0, w: 38.0, h: 100, title: 'Trang PDF', text: 'Vẫn hiện bên phải để đối chiếu trong lúc sửa.' }
      ]
    },
    {
      id: 'home-approver', title: 'Việc phê duyệt', img: 'img/home-approver.webp', roles: ['approver'],
      lead: 'Trang đầu tiên của người phê duyệt.',
      spots: [
        { x: 6.7, y: 14.0, w: 86.7, h: 13.0, title: 'Chờ tôi phê duyệt', text: 'Văn bản mà mọi người chỉnh sửa đã nộp. Bấm Phê duyệt để mở.' },
        { x: 85.1, y: 20.2, w: 7.1, h: 3.6, title: 'Phê duyệt', text: 'Mở màn soát ở bản chốt của văn bản.' },
        { x: 6.7, y: 30.1, w: 86.7, h: 13.0, title: 'Đang chỉnh sửa', text: 'Văn bản còn người chưa nộp. Xem được tiến độ, chưa phê duyệt được.' }
      ]
    },
    {
      id: 'review-approver', title: 'So sánh các bản', img: 'img/review-approver.webp', roles: ['approver', 'admin'],
      lead: 'Người phê duyệt và Admin thấy bản OCR gốc và bản của từng người chỉnh sửa trên cùng một đơn vị.',
      spots: [
        { x: 1.1, y: 12.4, w: 20.6, h: 2.3, title: 'Trạng thái và người làm', text: 'Chờ phê duyệt; dấu ✓ cạnh tên người chỉnh sửa là người đó đã nộp.' },
        { x: 19.5, y: 31.3, w: 40.0, h: 60.0, title: 'So sánh các bản', text: 'Mỗi người một màu. Phần khác bản OCR gốc được tô màu của người đó. Bấm Chọn bản này để lấy làm bản chốt, rồi Duyệt.' },
        { x: 50.6, y: 37.9, w: 8.2, h: 2.3, title: 'Đang là bản chốt', text: 'Bản đang được dùng làm bản chốt. Xuất JSON lấy bản này.' },
        { x: 76.0, y: 10.3, w: 5.7, h: 3.6, title: 'Trả lại', text: 'Mở ô ghi chú (bắt buộc), ví dụ: Điều 5 còn sai dấu. Văn bản quay về người chỉnh sửa.' },
        { x: 81.9, y: 10.3, w: 17.0, h: 3.6, title: 'Hoàn tất phê duyệt', text: 'Chỉ bấm được khi mọi đơn vị trên bản chốt đã được duyệt.' },
        { x: 19.5, y: 95.3, w: 40.0, h: 3.6, title: 'Nút thẩm định', text: 'Người phê duyệt Duyệt, Gắn cờ, báo Lỗi cấu trúc; không có nút Sửa chữ.' }
      ]
    },
    {
      id: 'library', title: 'Kho văn bản', img: 'img/library.webp',
      lead: 'Mục Văn bản: mọi văn bản, lọc theo chủ đề và trạng thái.',
      spots: [
        { x: 53.8, y: 1.1, w: 19.4, h: 3.6, title: 'Tìm văn bản', text: 'Theo số hiệu (254/2026) hoặc một phần tên. Có ở mọi màn hình.' },
        { x: 6.7, y: 14.2, w: 17.4, h: 29.3, title: 'Bộ lọc', text: 'Chủ đề I đến X và trạng thái quy trình. Bộ lọc đang dùng hiện thành thẻ phía trên bảng; bấm × để bỏ.' },
        { x: 25.7, y: 14.3, w: 67.5, h: 26.8, title: 'Danh sách', text: 'Bấm cả hàng để mở màn soát. Bấm tiêu đề cột để sắp xếp.' },
        { x: 87.5, y: 8.4, w: 5.8, h: 3.6, title: 'Tải lên', text: 'Chỉ Admin. PDF, kèm JSON nếu có; bản scan được nhận dạng chữ tự động.', roles: ['admin'] }
      ]
    },
    {
      id: 'home-admin', title: 'Tổng quan', img: 'img/home-admin.webp', roles: ['admin'],
      lead: 'Trang đầu tiên của Admin.',
      spots: [
        { x: 16.5, y: 0, w: 29.4, h: 5.7, title: 'Mục của Admin', text: 'Thêm Phân công, Thống kê và Tài khoản.' },
        { x: 6.7, y: 14.2, w: 86.7, h: 9.2, title: 'Số văn bản theo trạng thái', text: 'Bấm một ô để mở kho văn bản đã lọc theo trạng thái đó.' },
        { x: 6.7, y: 26.6, w: 42.5, h: 10.2, title: 'Cần chú ý', text: 'Văn bản chờ phê duyệt, bị trả lại hoặc chưa giao.' },
        { x: 50.8, y: 26.6, w: 42.5, h: 8.6, title: 'Theo chủ đề', text: 'Tiến độ của từng chủ đề.' }
      ]
    },
    {
      id: 'assign', title: 'Phân công', img: 'img/assign.webp', roles: ['admin'],
      lead: 'Giao văn bản cho người chỉnh sửa và người phê duyệt.',
      spots: [
        { x: 78.1, y: 17.9, w: 15.3, h: 3.6, title: 'Lọc nhanh', text: 'Gõ số hiệu hoặc tên để chỉ hiện văn bản khớp.' },
        { x: 6.7, y: 24.7, w: 86.6, h: 23.4, title: 'Bảng văn bản', text: 'Tích ô đầu hàng để chọn; ô trên tiêu đề chọn mọi văn bản đang hiện. Cột người được giao có dấu × để bỏ giao.' },
        { x: 74.5, y: 32.1, w: 1.1, h: 1.4, title: 'Bỏ giao', text: 'Bỏ một người khỏi văn bản. Bản riêng của người đó vẫn được giữ.' },
        { x: 6.7, y: 49.9, w: 86.7, h: 6.4, title: 'Thanh giao việc', text: 'Chọn người chỉnh sửa (một hoặc nhiều) và người phê duyệt rồi bấm Giao văn bản đã đánh dấu. Khi đang lọc một chủ đề có thêm nút Giao cả chủ đề.' }
      ]
    },
    {
      id: 'users', title: 'Tài khoản', img: 'img/users.webp', roles: ['admin'],
      lead: 'Chỉ Admin tạo và quản lý tài khoản.',
      spots: [
        { x: 69.6, y: 8.4, w: 19.4, h: 3.6, title: 'Tìm tài khoản', text: 'Lọc theo tên.' },
        { x: 69.6, y: 12.9, w: 8.4, h: 3.6, title: 'Tạo tài khoản', text: 'Tên đăng nhập, tên hiển thị, mật khẩu ban đầu và vai trò.' },
        { x: 54.6, y: 30.7, w: 8.1, h: 3.1, title: 'Vai trò và khóa', text: 'Đổi vai trò hoặc khóa, mở khóa. Không tự đổi quyền của chính mình được.' },
        { x: 73.2, y: 30.7, w: 8.4, h: 3.1, title: 'Đặt lại mật khẩu', text: 'Đặt mật khẩu mới khi người dùng quên.' }
      ]
    },
    {
      id: 'stats', title: 'Thống kê', img: 'img/stats.webp', roles: ['admin'],
      lead: 'Tiến độ theo người và theo văn bản.',
      spots: [
        { x: 6.7, y: 14.0, w: 86.7, h: 9.2, title: 'Tổng quan', text: 'Số văn bản theo trạng thái.' },
        { x: 6.7, y: 26.4, w: 86.7, h: 26.9, title: 'Theo người', text: 'Số văn bản đã xong trên số được giao, số đơn vị đã duyệt, sửa, gắn cờ của từng người.' },
        { x: 6.7, y: 56.3, w: 86.7, h: 15.6, title: 'Theo văn bản', text: 'Tiến độ từng văn bản. Bấm tiêu đề cột để sắp xếp.' }
      ]
    }
  ],

  shortcuts: [
    { keys: ['A'], group: 'Màn soát', text: 'Duyệt đơn vị đang mở rồi sang đơn vị chưa soát kế tiếp' },
    { keys: ['E'], group: 'Màn soát', text: 'Sửa chữ: chỉ còn trang PDF và ô sửa' },
    { keys: ['F'], group: 'Màn soát', text: 'Gắn cờ: mở ô ghi lý do' },
    { keys: ['J'], group: 'Màn soát', text: 'Sang đơn vị kế tiếp' },
    { keys: ['K'], group: 'Màn soát', text: 'Về đơn vị trước' },
    { keys: ['Ctrl', 'Enter'], group: 'Khi sửa chữ', text: 'Lưu chữ đã sửa' },
    { keys: ['Esc'], group: 'Khi sửa chữ', text: 'Hủy sửa, đóng ô lý do, đóng menu' },
    { keys: ['?'], group: 'Mọi màn hình', text: 'Mở bảng phím tắt' },
    { keys: ['←', '→'], group: 'Khi xem hướng dẫn', text: 'Bước trước, bước sau' }
  ],

  /* Bài tập: ảnh cắt từ ND254_2026 (scripts/build_guide_exercises.py, cùng thứ tự). answer: approve, edit, flag, structure_error.
     correct: chữ đúng (bài edit). fixes: các chỗ sai để giải thích. */
  exercises: [
    {
      unit: 'Điểm a, Khoản 1, Điều 5', img: 'img/ex-1.webp',
      ocr: 'a) Tính an toàn, bảo mật, toàn vẹn, đầy đủ, không bị thay đổi, sai lệch trong suốt thời gian lưu trữ;',
      answer: 'approve',
      explain: 'Chữ OCR khớp từng chữ với bản PDF, kể cả dấu câu. Đơn vị đúng thì Duyệt ngay, không cần sửa.'
    },
    {
      unit: 'Điểm h, Khoản 4, Điều 9', img: 'img/ex-2.webp',
      ocr: 'h) Đối với trường hợp bán xăng, dầu tại các cửa hàng bán lẽ cho khách hàng, thời điềm lập hóa đơn là thời điểm kết thúc việc bán xăng, dầu theo từng lần bán;',
      answer: 'edit',
      correct: 'h) Đối với trường hợp bán xăng, dầu tại các cửa hàng bán lẻ cho khách hàng, thời điểm lập hóa đơn là thời điểm kết thúc việc bán xăng, dầu theo từng lần bán;',
      fixes: [['bán lẽ', 'bán lẻ'], ['thời điềm', 'thời điểm']],
      explain: 'Hai lỗi sai dấu, loại lỗi OCR hay gặp nhất: dấu ngã và dấu hỏi, dấu huyền và dấu hỏi dễ bị nhận nhầm. Sửa rồi lưu.'
    },
    {
      unit: 'Điểm g, Khoản 1, Điều 10', img: 'img/ex-3.webp',
      ocr: 'g) Thời điểm lập hóa đơn thực hiện theo hướng dẫn tại Điều 6 Nghị định này và được hiển thị theo định dạng ngày, tháng, năm của năm dương lịch;',
      answer: 'edit',
      correct: 'g) Thời điểm lập hóa đơn thực hiện theo hướng dẫn tại Điều 9 Nghị định này và được hiển thị theo định dạng ngày, tháng, năm của năm dương lịch;',
      fixes: [['Điều 6', 'Điều 9']],
      explain: 'Sai số điều dẫn chiếu: bản PDF ghi Điều 9. Số hiệu, ngày tháng, số tiền phải so từng chữ số vì sai một số là sai nội dung pháp lý.'
    },
    {
      unit: 'Điểm a, Khoản 3, Điều 5', img: 'img/ex-4.webp',
      ocr: 'a) Hoá đơn, chứng từ chưa lập được lưu trữ, bảo quản trong kho theo chế độ lưu trữ bảo quản chứng từ có giá; b) Hoá đơn, chứng từ đã lập trong các đơn vị kế toán được lưu trữ theo quy định lưu trữ, bảo quản chứng từ kế toán;',
      answer: 'structure_error',
      explain: 'Đơn vị là điểm a nhưng chứa luôn nội dung điểm b. Đây là lỗi tách cấu trúc, không sửa bằng cách xóa chữ: báo Lỗi cấu trúc và ghi rõ, ví dụ: điểm a gộp cả điểm b.'
    },
    {
      unit: 'Điểm d, Khoản 2, Điều 6', img: 'img/ex-5.webp',
      ocr: 'd) Trình tự, thủ t?c c?p hóa đ?n điện tử theo từng l?n phát sinh tại khoản này theo quy định của Bộ tr??ng Bộ Tài chính.',
      answer: 'flag',
      explain: 'Bản scan quá mờ để đối chiếu chắc chắn, chữ OCR lại có ký tự lạ. Không đoán: Gắn cờ và ghi lý do, ví dụ: trang 10 mờ, cần bản scan rõ hơn.'
    },
    {
      unit: 'Điểm e, Khoản 1, Điều 10', img: 'img/ex-6.webp',
      ocr: 'e) Chữ ký của người bán, chữ ký của người mua: Trên hóa đơn điện tử nhất thiết phải có chữ ký số của người mua (trừ trường hợp người mua, người bán có thỏa thuận);',
      answer: 'edit',
      correct: 'e) Chữ ký của người bán, chữ ký của người mua: Trên hóa đơn điện tử không nhất thiết phải có chữ ký số của người mua (trừ trường hợp người mua, người bán có thỏa thuận);',
      fixes: [['điện tử nhất thiết', 'điện tử không nhất thiết']],
      explain: 'OCR làm rơi chữ "không" ở đầu dòng thứ hai, nghĩa của quy định bị đảo ngược. Đọc theo nghĩa, không chỉ dò chính tả.'
    },
    {
      unit: 'Điểm b, Khoản 2, Điều 1', img: 'img/ex-7.webp',
      ocr: 'b) Lập hóa đơn điện tử có mã của cơ quan thuế; lập hóa đơn điện tử không có mã của cơ quan thuế; xử lý sự cố; ủy nhiệm lập biên lai;',
      answer: 'approve',
      explain: 'Đơn vị đúng. Sau vài bài có lỗi, dễ nghi ngờ mọi thứ; đã so kỹ mà không thấy sai thì Duyệt.'
    }
  ],

  admin: [
    { title: 'Cấp tài khoản', steps: ['Tài khoản > Tạo tài khoản.', 'Nhập tên đăng nhập (chữ thường, không dấu), tên hiển thị, mật khẩu ban đầu.', 'Chọn vai trò: Người chỉnh sửa, Người phê duyệt hoặc Admin.', 'Gửi tên và mật khẩu cho người dùng; họ đổi mật khẩu ở menu tài khoản.'], screen: 'users' },
    { title: 'Nhập văn bản', steps: ['Văn bản > Tải lên.', 'Chọn chủ đề, chọn tệp PDF (kèm JSON nếu đã có).', 'App tự tách Chương, Điều, Khoản, Điểm; bản scan được nhận dạng chữ.', 'Kiểm tra số đơn vị và các đơn vị Không chắc trước khi giao.'], screen: 'library' },
    { title: 'Giao việc', steps: ['Phân công > lọc theo chủ đề nếu cần.', 'Tích các văn bản cần giao.', 'Chọn một hoặc nhiều người chỉnh sửa và một người phê duyệt.', 'Bấm Giao văn bản đã đánh dấu. Giao cả chủ đề khi đang lọc một chủ đề.'], screen: 'assign' },
    { title: 'Theo dõi và mở lại', steps: ['Tổng quan > Cần chú ý: văn bản chờ phê duyệt, bị trả lại, chưa giao.', 'Thống kê: cân lại khối lượng giữa các người.', 'Văn bản đã phê duyệt cần sửa: mở văn bản, bấm Mở lại.', 'Khóa tài khoản người nghỉ việc ở Tài khoản; lịch sử thẩm định vẫn giữ nguyên.'], screen: 'stats' }
  ],

  faq: [
    { q: 'Đăng nhập báo tạm khóa, phải làm gì?', a: 'Nhập sai mật khẩu 10 lần trong 15 phút thì tài khoản bị chặn đăng nhập tạm thời. Đợi 15 phút rồi thử lại, hoặc nhờ Admin đặt lại mật khẩu.' },
    { q: 'Quên mật khẩu?', a: 'Không có tự khôi phục mật khẩu. Liên hệ Admin để đặt lại, sau đó đổi mật khẩu ở menu tài khoản (góc trên bên phải).' },
    { q: 'Vì sao tôi không thấy nút Duyệt, Sửa chữ?', a: 'Có ba lý do thường gặp: văn bản chưa giao cho bạn; bạn đã bấm Hoàn thành văn bản (chỉ còn xem được tới khi bị trả lại); hoặc app đang ở chế độ chỉ xem vì chưa kết nối được cơ sở dữ liệu. Dòng chữ có biểu tượng khóa trên khung giữa cho biết lý do cụ thể.', roles: ['reviewer', 'approver'] },
    { q: 'Người khác có thấy chỗ tôi sửa không?', a: 'Không. Mỗi người chỉnh sửa có bản riêng. Chỉ người phê duyệt và Admin thấy các bản này trong So sánh các bản, sau khi văn bản chuyển sang phê duyệt.' },
    { q: 'Sửa chữ xong có cần Duyệt không?', a: 'Có. Lưu chữ chỉ ghi nội dung mới; trạng thái của đơn vị vẫn giữ nguyên cho tới khi bạn bấm Duyệt ([[A]]).', roles: ['reviewer'] },
    { q: 'Khung vàng trên PDF nét đứt nghĩa là gì?', a: 'Trang là bản scan không có lớp chữ, nên vị trí của đơn vị chỉ là ước lượng theo bố cục. Đọc rộng ra một chút quanh khung.' },
    { q: 'Khi nào dùng Gắn cờ, khi nào dùng Lỗi cấu trúc?', a: 'Gắn cờ khi nội dung cần hỏi lại: bản scan mờ, chữ đáng ngờ, nghi bản gốc in sai. Lỗi cấu trúc khi đơn vị bị tách sai: gộp hai khoản, cắt đôi một điểm, sai số hiệu. Cả hai đều bắt buộc ghi lý do.' },
    { q: 'Văn bản bị trả lại, xem ghi chú ở đâu?', a: 'Ở mục Bị trả lại, cần sửa trên Việc của tôi, và trên đầu màn soát của văn bản đó.', roles: ['reviewer'] },
    { q: 'Vì sao chưa bấm được Hoàn tất phê duyệt?', a: 'Mọi đơn vị trên bản chốt phải ở trạng thái Đã duyệt. Dùng nút lọc Chưa soát trên cây cấu trúc để tìm đơn vị còn thiếu.', roles: ['approver'] },
    { q: 'Nhãn Không chắc trên cây là gì?', a: 'Bộ tách văn bản không chắc đơn vị đó được tách đúng (ví dụ ranh giới giữa hai điểm không rõ). Soát kỹ cấu trúc của các đơn vị này.' },
    { q: 'App có tự lưu chỗ tôi đang soát không?', a: 'Có. Lần sau mở văn bản, app mở đúng đơn vị bạn soát gần nhất; trang Việc của tôi có nút Tiếp tục.' },
    { q: 'Muốn xem lại hướng dẫn từng bước trong app?', a: 'Menu Hướng dẫn trên thanh trên cùng > Xem hướng dẫn màn hình này. Mỗi màn hình có hướng dẫn riêng theo vai trò của bạn.' }
  ]
};
