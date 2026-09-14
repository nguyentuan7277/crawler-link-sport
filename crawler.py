#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sport M3U8 Playlist Crawler
Crawl lịch thi đấu và link stream HLS trực tiếp từ nhiều nguồn: Chuối Chiên TV, Cola TV, Xoilac TV.
Ưu tiên chất lượng: FULL HD -> HD -> SD.
Xuất file sport.m3u8 tương thích 100% với TiviMate, VLC, OTT Navigator.
Mỗi nguồn được gắn tiền tố riêng trong group-title để không bị trộn lẫn.
"""

import re
import sys
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

DEFAULT_LOGO = "https://media.chuoichientv.com/media/uploads/default-thumbnail.png"

# ---------------------------------------------------------------------------
# Chuối Chiên TV
# ---------------------------------------------------------------------------
CHUOI_API_URL = "https://api-v2.chuoichientv.net/v2/matches"
CHUOI_REFERER = "https://live05.chuoichientv.me/"
CHUOI_ORIGIN = "https://live05.chuoichientv.me"
CHUOI_SOURCE_TAG = "ChuoiTV"


def fetch_matches_chuoi():
    """Lấy danh sách các trận đấu từ API v2 của hệ thống Chuối Chiên"""
    req = urllib.request.Request(CHUOI_API_URL)
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Origin", CHUOI_ORIGIN)
    req.add_header("Referer", CHUOI_REFERER)
    req.add_header("Accept", "application/json, text/plain, */*")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("matches", [])
    except Exception as e:
        print(f"[-] [ChuoiTV] Lỗi khi gọi API: {e}", file=sys.stderr)
        return []


def select_best_stream(streams):
    """
    Chọn link stream tối ưu theo thứ tự ưu tiên:
    1. FULL HD (1080p)
    2. HD (720p)
    3. Link stream đầu tiên khả dụng
    """
    if not streams:
        return None, None

    for s in streams:
        label = (s.get("label") or "").upper().strip()
        url = s.get("url") or s.get("streamUrl")
        if url and ("FULL HD" in label or "FHD" in label or "1080" in label):
            return url, label

    for s in streams:
        label = (s.get("label") or "").upper().strip()
        url = s.get("url") or s.get("streamUrl")
        if url and ("HD" in label or "720" in label):
            return url, label

    first = streams[0]
    first_url = first.get("url") or first.get("streamUrl")
    first_label = (first.get("label") or "SD").upper().strip()
    return first_url, first_label


def format_time_vn_iso(utc_iso_str):
    """Chuyển đổi giờ UTC (ISO string) sang giờ Việt Nam (GMT+7) dạng HH:mm DD/MM"""
    if not utc_iso_str:
        return ""
    try:
        clean_str = utc_iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_str)
        tz_vn = timezone(timedelta(hours=7))
        dt_vn = dt.astimezone(tz_vn)
        return dt_vn.strftime("%H:%M %d/%m")
    except Exception:
        return utc_iso_str


def format_time_vn_unix(ts):
    """Chuyển đổi unix timestamp (giây) sang giờ Việt Nam (GMT+7) dạng HH:mm DD/MM"""
    if not ts:
        return ""
    try:
        tz_vn = timezone(timedelta(hours=7))
        dt_vn = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(tz_vn)
        return dt_vn.strftime("%H:%M %d/%m")
    except Exception:
        return ""


def build_channels_chuoi(matches):
    """Chuẩn hóa dữ liệu trận đấu từ Chuối Chiên TV thành danh sách channel chung"""
    channels = []
    for match in matches:
        home = match.get("teams", {}).get("home", {}).get("name", "Đội nhà")
        away = match.get("teams", {}).get("away", {}).get("name", "Đội khách")
        home_logo = match.get("teams", {}).get("home", {}).get("logo", "")

        league = match.get("league", {}).get("name", "Bóng Đá")
        league_logo = match.get("league", {}).get("logo", "")
        logo = home_logo or league_logo or DEFAULT_LOGO

        status = match.get("status", "")
        match_time = match.get("matchTime", "")
        time_vn = format_time_vn_iso(match_time)
        status_prefix = "● [LIVE] " if status == "live" else f"[{time_vn}] "

        blvs = match.get("blvs", []) or []
        if not blvs:
            blvs = match.get("blvs_bonglau", []) or match.get("blvs_nguoitho", []) or []
        if not blvs:
            continue

        for blv in blvs:
            blv_name = blv.get("name") or "BLV"
            streams = blv.get("streams") or []
            stream_url, quality = select_best_stream(streams)
            if not stream_url:
                continue

            tvg_id = f"cctv_{match.get('externalId', 'x')}_{blv.get('username', 'blv')}"
            channels.append({
                "source_tag": CHUOI_SOURCE_TAG,
                "status_prefix": status_prefix,
                "home": home,
                "away": away,
                "logo": logo,
                "league": league,
                "channel_suffix": f" - {blv_name} [{quality}]",
                "tvg_id": tvg_id,
                "stream_url": stream_url,
                "referer": CHUOI_REFERER,
                "origin": CHUOI_ORIGIN,
            })

    return channels


# ---------------------------------------------------------------------------
# Cola TV
# ---------------------------------------------------------------------------
COLA_API_URL = "https://api.gvapi.cc/api/matches"
COLA_REFERER = "https://colatvttbdh.tv/"
COLA_ORIGIN = "https://colatvttbdh.tv"
COLA_SOURCE_TAG = "ColaTV"


def fetch_matches_cola():
    """Lấy danh sách các trận đấu từ API của hệ thống Cola TV"""
    url = f"{COLA_API_URL}?t={int(time.time() * 1000)}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Origin", COLA_ORIGIN)
    req.add_header("Referer", COLA_REFERER)
    req.add_header("Accept", "application/json, text/plain, */*")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            matches = data.get("data") or {}
            return list(matches.values())
    except Exception as e:
        print(f"[-] [ColaTV] Lỗi khi gọi API: {e}", file=sys.stderr)
        return []


def build_channels_cola(matches):
    """Chuẩn hóa dữ liệu trận đấu từ Cola TV thành danh sách channel chung"""
    channels = []
    for match in matches:
        stream_url = match.get("video_url") or match.get("videoUrl")
        # API trả "https" (placeholder) khi trận chưa mở luồng - bỏ qua
        if not stream_url or not stream_url.startswith("http") or ".m3u8" not in stream_url:
            continue

        home_team = match.get("home_team") or {}
        away_team = match.get("away_team") or {}
        competition = match.get("competition") or {}

        home = home_team.get("name") or match.get("homeTeamName") or "Đội nhà"
        away = away_team.get("name") or match.get("awayTeamName") or "Đội khách"
        logo = home_team.get("logo") or competition.get("logo") or DEFAULT_LOGO
        league = competition.get("name") or match.get("competitionName") or "Bóng Đá"

        status = match.get("match_status") or match.get("matchStatus")
        match_time = match.get("match_time") or match.get("matchTime")
        time_vn = format_time_vn_unix(match_time)
        status_prefix = "● [LIVE] " if status == "live" else f"[{time_vn}] "

        match_id = match.get("match_id") or match.get("matchId") or "x"
        channels.append({
            "source_tag": COLA_SOURCE_TAG,
            "status_prefix": status_prefix,
            "home": home,
            "away": away,
            "logo": logo,
            "league": league,
            "channel_suffix": "",
            "tvg_id": f"cola_{match_id}",
            "stream_url": stream_url,
            "referer": COLA_REFERER,
            "origin": COLA_ORIGIN,
        })

    return channels


# ---------------------------------------------------------------------------
# Xoilac TV
# ---------------------------------------------------------------------------
XOILAC_SCHEDULE_URL = "https://data-api.sportflowlivez.com/v1/football/xoilac365/match/live"
XOILAC_MATCH_DETAIL_URL = "https://fb-api.sportliveapiz.com/football/match/{}"
XOILAC_SITE_URL = "https://xoilacxba.tv"
XOILAC_REFERER = "https://xoilacxba.tv/"
XOILAC_ORIGIN = "https://xoilacxba.tv"
XOILAC_SOURCE_TAG = "XoilacTV"
# 1=chưa đá, 8=đã kết thúc, còn lại là các trạng thái đang diễn ra (hiệp 1/hiệp 2/nghỉ...)
XOILAC_NOT_LIVE_STATUS = (1, 8, 9)


def _http_get(url, referer=None, timeout=10):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/json, text/html, */*")
    if referer:
        req.add_header("Referer", referer)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def fetch_matches_xoilac():
    """Lấy danh sách trận đang diễn ra (live) từ hệ thống Xoilac TV"""
    try:
        raw = _http_get(XOILAC_SCHEDULE_URL, referer=XOILAC_REFERER)
        data = json.loads(raw)
        matches = data.get("matches", []) or []
        return [m for m in matches if m.get("status_id") not in XOILAC_NOT_LIVE_STATUS]
    except Exception as e:
        print(f"[-] [XoilacTV] Lỗi khi gọi API lịch thi đấu: {e}", file=sys.stderr)
        return []


def _xoilac_fetch_team_info(match_id):
    """Lấy tên/logo đội bóng và giải đấu theo matchId (trang không nhúng sẵn tên đội)"""
    try:
        raw = _http_get(XOILAC_MATCH_DETAIL_URL.format(match_id), referer=XOILAC_REFERER)
        data = json.loads(raw).get("data", {}) or {}
        home = data.get("home_team", {}) or {}
        away = data.get("away_team", {}) or {}
        competition = data.get("competition", {}) or {}
        return {
            "home": home.get("name") or "Đội nhà",
            "away": away.get("name") or "Đội khách",
            "logo": home.get("logo") or competition.get("logo") or DEFAULT_LOGO,
            "league": competition.get("name") or "Bóng Đá",
        }
    except Exception:
        return None


def _xoilac_resolve_stream(match_page_url):
    """
    Trang trận đấu nhúng sẵn (server-render, không cần JS) danh sách link nhúng
    dạng `list_stream = [["https://xl365.domainkqt.cc/ajax/chanel/type/X/link/channelY"], ...]`.
    Mở từng link nhúng (với Referer là trang trận đấu) để lấy ra link .m3u8/.flv thật từ CDN.
    """
    try:
        html = _http_get(match_page_url, referer=XOILAC_REFERER)
    except Exception:
        return None, None

    m = re.search(r'list_stream\s*=\s*(\[.*?\]);', html)
    if not m:
        return None, None

    try:
        embed_urls = [item[0] for item in json.loads(m.group(1)) if item]
    except Exception:
        return None, None

    for embed_url in embed_urls:
        try:
            embed_html = _http_get(embed_url, referer=match_page_url)
        except Exception:
            continue

        found = re.findall(r'https?:[^"\'\s]*\.(?:m3u8|flv)[^"\'\s]*', embed_html)
        if not found:
            continue

        m3u8_urls = [u for u in found if ".m3u8" in u]
        if m3u8_urls:
            return m3u8_urls[0], embed_url

        # Trang nhúng chỉ trả .flv (dùng cho flv.js), nhưng CDN cũng phục vụ .m3u8
        # cùng path/query - suy ra bản HLS để tương thích ExoPlayer/TiviMate (không hỗ trợ FLV thô).
        return found[0].replace(".flv", ".m3u8", 1), embed_url

    return None, None


def build_channels_xoilac(matches):
    """Chuẩn hóa dữ liệu trận đấu từ Xoilac TV thành danh sách channel chung"""
    channels = []
    for match in matches:
        match_id = match.get("id")
        slug = match.get("slug")
        if not match_id or not slug:
            continue

        match_page_url = f"{XOILAC_SITE_URL}{slug}/"
        stream_url, embed_url = _xoilac_resolve_stream(match_page_url)
        if not stream_url:
            continue

        info = _xoilac_fetch_team_info(match_id)
        if not info:
            continue

        time_vn = format_time_vn_unix(match.get("match_time"))
        status_prefix = "● [LIVE] "  # Danh sách đã lọc chỉ còn các trận đang diễn ra

        embed_origin = None
        if embed_url:
            m = re.match(r'(https?://[^/]+)', embed_url)
            embed_origin = m.group(1) if m else None

        channels.append({
            "source_tag": XOILAC_SOURCE_TAG,
            "status_prefix": status_prefix,
            "home": info["home"],
            "away": info["away"],
            "logo": info["logo"],
            "league": info["league"],
            "channel_suffix": "",
            "tvg_id": f"xoilac_{match_id}",
            "stream_url": stream_url,
            # CDN của Xoilac kiểm tra Referer phải là trang nhúng (embed), không phải trang chủ
            "referer": embed_url or XOILAC_REFERER,
            "origin": embed_origin or XOILAC_ORIGIN,
        })

    return channels


# ---------------------------------------------------------------------------
# Xuất file M3U8
# ---------------------------------------------------------------------------

def generate_m3u8(channels, output_file="sport.m3u8"):
    """Tạo file playlist m3u8 từ danh sách channel đã chuẩn hóa (nhiều nguồn)"""
    tz_vn = timezone(timedelta(hours=7))
    now_vn = datetime.now(tz_vn).strftime("%H:%M:%S %d/%m/%Y")

    lines = [
        "#EXTM3U x-tvg-url=\"\"",
        "## Playlist Thể Thao Tự Động - Nguồn: Chuối Chiên TV, Cola TV",
        f"## Cập nhật lúc: {now_vn}",
        ""
    ]

    count = 0
    for ch in channels:
        tivimate_stream_url = (
            f"{ch['stream_url']}|Referer={ch['referer']}&Origin={ch['origin']}&User-Agent={USER_AGENT}"
        )
        channel_name = f"{ch['status_prefix']}{ch['home']} vs {ch['away']}{ch['channel_suffix']} [{ch['source_tag']}]"
        group_title = f"[{ch['source_tag']}] {ch['league']}"

        lines.append(
            f'#EXTINF:-1 tvg-id="{ch["tvg_id"]}" tvg-name="{ch["home"]} vs {ch["away"]}" '
            f'tvg-logo="{ch["logo"]}" group-title="{group_title}",{channel_name}'
        )
        lines.append(f"#EXTVLCOPT:http-referrer={ch['referer']}")
        lines.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
        lines.append(tivimate_stream_url)
        lines.append("")
        count += 1

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"[+] Đã tạo thành công file '{output_file}' với {count} kênh/luồng stream!")


if __name__ == "__main__":
    all_channels = []

    print("[*] Đang tải lịch thi đấu từ Chuối Chiên TV...")
    chuoi_matches = fetch_matches_chuoi()
    print(f"[+] [ChuoiTV] Đã lấy được {len(chuoi_matches)} trận đấu.")
    all_channels.extend(build_channels_chuoi(chuoi_matches))

    print("[*] Đang tải lịch thi đấu từ Cola TV...")
    cola_matches = fetch_matches_cola()
    print(f"[+] [ColaTV] Đã lấy được {len(cola_matches)} trận đấu.")
    all_channels.extend(build_channels_cola(cola_matches))

    print("[*] Đang tải lịch thi đấu từ Xoilac TV...")
    xoilac_matches = fetch_matches_xoilac()
    print(f"[+] [XoilacTV] Đã lấy được {len(xoilac_matches)} trận đang diễn ra.")
    all_channels.extend(build_channels_xoilac(xoilac_matches))

    if not all_channels:
        print("[-] Không lấy được dữ liệu kênh nào từ các nguồn. Đang thử lại hoặc kết thúc.")
        sys.exit(1)

    generate_m3u8(all_channels, "sport.m3u8")
