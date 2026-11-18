# Trang hướng dẫn sử dụng (`/help`)

App phục vụ trang này ở `/help`, chuyển hướng tới `/static/guide/index.html`. Không cần đăng nhập, vì trang không chứa
dữ liệu nội bộ. Mở trực tiếp `index.html` cũng chạy được (không cần app). Khi đó icon trên thanh trên cùng không hiện;
nút sáng/tối đổi sang chữ.

| Tệp | Nội dung |
|-----|----------|
| `index.html` | Khung trang và mục lục |
| `data.js` | Toàn bộ nội dung: vai trò, quy trình, màn hình và điểm nóng, phím tắt, bài tập, quản trị, FAQ |
| `guide.js` | Dựng các mục từ `data.js`, lọc theo vai trò, chấm bài tập |
| `guide.css` | Bố cục riêng của trang; màu, nút, `kbd` lấy từ `../style.css` |
| `img/*.webp` | Ảnh chụp màn hình 1440x900 và ảnh cắt cho bài tập |

Hướng dẫn từng bước bên trong app là phần riêng: `../tour.js` và `../tours.json`. Nội dung hai phần phải khớp nhau
khi đổi tên nút hoặc quy trình.

## Khi giao diện đổi

`tests/test_guide.py` kiểm tra:
- mỗi selector trong `tours.json` vẫn có trong template;
- mọi ảnh mà `data.js` nhắc tới đều tồn tại;
- số bài tập khớp với script cắt ảnh.

Test không kiểm tra được ảnh chụp có còn giống giao diện hay không. Đổi bố cục thì phải chụp lại.

### Chụp lại ảnh màn hình

1. Chạy app ở chế độ PostgreSQL trên một CSDL riêng để thử, không dùng CSDL thật, với `AUTO_IMPORT_DIR=data`. Tạo ba
   tài khoản thử: hai người chỉnh sửa và một người phê duyệt.
2. Giao ND254_2026 cho cả hai người chỉnh sửa và ND255_2026 cho một người, cùng một người phê duyệt.
3. Dựng dữ liệu cho các trang:
   - Mỗi người chỉnh sửa duyệt vài đơn vị ND254_2026 và bấm Hoàn thành văn bản. Trong đó một đơn vị gắn cờ, một đơn vị
     sửa chữ, một đơn vị báo lỗi cấu trúc.
   - Người được giao ND255_2026 duyệt khoảng chục đơn vị mà chưa nộp, để trang chủ có cả khối Đang làm và Đã nộp.
4. Chụp ở khung nhìn 1440x900, giao diện sáng, chưa mở hướng dẫn từng bước (đặt `tour:done:*` trong localStorage để
   không hiện lời mời). Lưu WebP chất lượng khoảng 80, giữ đúng tên tệp:

   | Tệp | Tài khoản | Địa chỉ |
   |-----|-----------|---------|
   | `login.webp` | chưa đăng nhập | `/login` |
   | `home-reviewer.webp` | người chỉnh sửa | `/` |
   | `review-editor.webp` | người chỉnh sửa | `/documents/ND255_2026?unit=ND255_2026_D4_K4` |
   | `review-focus-edit.webp` | người chỉnh sửa | như trên, sau khi nhấn E |
   | `home-approver.webp` | người phê duyệt | `/` |
   | `review-approver.webp` | người phê duyệt | `/documents/ND254_2026?unit=ND254_2026_D1_K1` |
   | `home-admin.webp`, `library.webp`, `assign.webp`, `users.webp`, `stats.webp` | admin | `/`, `/documents`, `/admin/assign`, `/admin/users`, `/admin/stats` |

5. Đo lại toạ độ điểm nóng trong `data.js` (`x`, `y`, `w`, `h` theo % khung nhìn). Chạy đoạn sau trong console của trang
   vừa chụp, thay danh sách selector bằng selector của các điểm nóng trên trang đó:

   ```js
   ['.stat-row', '#document-table'].map(s => { const r = document.querySelector(s).getBoundingClientRect();
     return [s, r.left / innerWidth * 100, r.top / innerHeight * 100, r.width / innerWidth * 100, r.height / innerHeight * 100]
       .map(v => typeof v === 'number' ? Math.round(v * 10) / 10 : v); })
   ```

### Ảnh bài tập

```bash
python scripts/build_guide_exercises.py
```

Script cắt vùng của từng đơn vị trên trang PDF của ND254_2026, giống khung tô vàng của màn soát. Danh sách đơn vị nằm
ở `EXERCISES` trong script. Chữ OCR đã cài lỗi và đáp án viết tay ở `exercises` trong `data.js`, giữ cùng thứ tự với
`EXERCISES`.

Khi thêm bài, phải kiểm tra bằng mắt rằng chữ trong ảnh khớp chữ `correct` (bài sửa chữ) hoặc chữ OCR (bài duyệt).
