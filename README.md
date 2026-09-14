# Crawl Lịch Bóng Đá & Stream Thể Thao (M3U8)

Dự án tự động crawl lịch thi đấu và link phát sóng trực tiếp (HLS `.m3u8`) từ nhiều nguồn: **Chuối Chiên TV** (`chuoichientv1.com`), **Cola TV** (`colatvttbdh.tv`) và **Xoilac TV** (`xoilacxba.tv`), ưu tiên chất lượng **FULL HD** (nếu không có thì lấy **HD**), định dạng chuẩn tương thích hoàn hảo với **TiviMate**, **VLC**, **OTT Navigator**, **Kodi**. Mỗi nguồn được gắn tiền tố riêng trong `group-title` (`[ChuoiTV]`, `[ColaTV]`, `[XoilacTV]`) để không bị trộn lẫn.

---

## 🌟 Tính Năng
- **Ưu tiên chất lượng hình ảnh:** Tự động chọn luồng `FULL HD` (1080p), nếu không có sẽ chọn `HD` (720p).
- **Phân loại & Thông tin chi tiết:**
  - Kèm logo đội bóng / giải đấu (`tvg-logo`).
  - Gom nhóm kênh theo giải đấu (`group-title` như: Asian Games, Serie A, Ngoại Hạng Anh, C1...).
  - Đánh dấu trạng thái `● [LIVE]` nếu trận đang đá hoặc hiển thị giờ đá theo giờ Việt Nam `[HH:mm DD/MM]`.
  - Hiển thị tên Bình Luận Viên (BLV).
- **Tương thích TiviMate:** Đã nhúng sẵn các thẻ `#EXTVLCOPT` và Referer Pipe (`|Referer=...&User-Agent=...`) để trình phát ExoPlayer của TiviMate phát mượt mà không bị chặn WAF/CORS.
- **Tự động cập nhật qua GitHub Actions:** Chạy cron job định kỳ mỗi 1 tiếng để cập nhật danh sách kênh mới nhất.

---

## 🚀 Cách Cài Đặt & Đẩy Lên GitHub

### 1. Khởi tạo Git và đẩy lên Repository của bạn
```bash
git init
git add .
git commit -m "Initial commit: crawler and github action"
git branch -M main
git remote add origin https://github.com/<tai-khoan-cua-ban>/crawl-sport-m3u.git
git push -u origin main
```

### 2. Cấp quyền Write cho GitHub Actions (Bắt buộc)
Để GitHub Action có thể tự động commit và push file `sport.m3u8` mới:
1. Vào repository trên GitHub -> chọn **Settings**.
2. Chọn mục **Actions** -> **General**.
3. Kéo xuống phần **Workflow permissions**:
   - Chọn **Read and write permissions**.
   - Bấm **Save**.

---

## 📺 Cách Nạp Vào TiviMate

1. Lấy link Raw của file `sport.m3u8` trên GitHub:
   ```text
   https://raw.githubusercontent.com/<tai-khoan-cua-ban>/crawl-sport-m3u/main/sport.m3u8
   ```
2. Mở app **TiviMate** trên TV hoặc TV Box:
   - Chọn **Add playlist** -> **M3U playlist**.
   - Nhập đường dẫn link Raw ở trên.
3. Trong cài đặt Playlist trên TiviMate:
   - Bật **Auto-update playlist on app start** (Tự cập nhật khi mở app).
   - Chọn chu kỳ cập nhật tự động (ví dụ: Mỗi 2 hoặc 4 tiếng).

---

## 🛠️ Chạy Thử Nghiệm Thủ Công (Local)

Script chỉ sử dụng thư viện tiêu chuẩn của Python 3, không cần cài đặt thêm gói ngoài:

```bash
python3 crawler.py
```
File `sport.m3u8` sẽ được sinh ra trực tiếp trong thư mục dự án.
