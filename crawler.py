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
import os
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone, timedelta

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

PROVIDER_GROUPS = {
    "ChuoiTV": "Chuối TV",
    "ColaTV": "Cola TV",
    "XoilacTV": "Xoilac TV",
}

DEFAULT_LOGO = "https://media.chuoichientv.com/media/uploads/default-thumbnail.png"
SOURCE_HEALTH = {}
SOURCE_HEALTH_STATE_FILE = os.getenv("SOURCE_HEALTH_STATE_FILE", ".source-health.json")
SOURCE_FAILURE_THRESHOLD = max(int(os.getenv("SOURCE_FAILURE_THRESHOLD", "3")), 1)


def record_source_health(source, healthy, detail=None):
    """Record the result of one source fetch for the end-of-run monitor."""
    SOURCE_HEALTH[source] = {"healthy": healthy, "detail": detail or "unknown error"}


def _send_telegram(message):
    """Send a Telegram message when credentials are provided by the VPS."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        return False

    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=payload,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return 200 <= response.status < 300
    except Exception as e:
        print(f"[-] Không gửi được Telegram notification: {type(e).__name__}", file=sys.stderr)
        return False


def notify_source_health():
    """Alert once per outage and once again when the source recovers."""
    try:
        with open(SOURCE_HEALTH_STATE_FILE, "r", encoding="utf-8") as state_file:
            state = json.load(state_file)
    except (OSError, json.JSONDecodeError):
        state = {}

    for source, result in SOURCE_HEALTH.items():
        source_state = state.setdefault(source, {"failures": 0, "alerted": False})
        if result["healthy"]:
            if source_state.get("alerted"):
                _send_telegram(f"✅ {source} đã hoạt động trở lại.")
            source_state.update({"failures": 0, "alerted": False})
            continue

        source_state["failures"] = source_state.get("failures", 0) + 1
        if (
            source_state["failures"] >= SOURCE_FAILURE_THRESHOLD
            and not source_state.get("alerted")
        ):
            _send_telegram(
                f"⚠️ {source} lỗi {source_state['failures']} lần liên tiếp. "
                f"Chi tiết: {result['detail']}"
            )
            # Mark it even if Telegram delivery failed, preventing a notification
            # attempt on every subsequent cron run.
            source_state["alerted"] = True

    temp_state_file = f"{SOURCE_HEALTH_STATE_FILE}.tmp"
    with open(temp_state_file, "w", encoding="utf-8") as state_file:
        json.dump(state, state_file, ensure_ascii=False)
    os.replace(temp_state_file, SOURCE_HEALTH_STATE_FILE)

# ---------------------------------------------------------------------------
# Chuối Chiên TV
# ---------------------------------------------------------------------------
CHUOI_API_URL = "https://api-v2.chuoichientv.net/v2/matches"
CHUOI_API_REFERER = "https://live05.chuoichientv.me/"
CHUOI_API_ORIGIN = "https://live05.chuoichientv.me"
# The public site embeds its player from this origin. ChuoiTV's stream CDNs
# enforce hotlink protection and reject the public page's origin with 403.
CHUOI_STREAM_REFERER = "https://live.chuoichien.tv/"
CHUOI_STREAM_ORIGIN = "https://live.chuoichien.tv"
CHUOI_SOURCE_TAG = "ChuoiTV"


def fetch_matches_chuoi():
    """Lấy danh sách các trận đấu từ API v2 của hệ thống Chuối Chiên"""
    req = urllib.request.Request(CHUOI_API_URL)
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Origin", CHUOI_API_ORIGIN)
    req.add_header("Referer", CHUOI_API_REFERER)
    req.add_header("Accept", "application/json, text/plain, */*")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            record_source_health("ChuốiTV API", True)
            return data.get("matches", [])
    except Exception as e:
        record_source_health("ChuốiTV API", False, str(e))
        print(f"[-] [ChuoiTV] Lỗi khi gọi API: {e}", file=sys.stderr)
        return []


def select_chuoi_stream(streams):
    """
    Chọn stream theo đúng thứ tự API, giống player chính thức của ChuốiTV.

    Nguồn đầu tiên thường là HD và ổn định hơn. Tự ưu tiên FHD làm crawler
    chọn khác website, nên có thể lấy phải CDN phụ không phát được trên app.
    """
    if not streams:
        return None, None

    for s in streams:
        url = s.get("url") or s.get("streamUrl")
        if url:
            label = (s.get("label") or "SD").upper().strip()
            return url, label

    return None, None


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


def unix_time_from_iso(utc_iso_str):
    """Convert an ISO datetime to a Unix timestamp for playlist ordering."""
    try:
        dt = datetime.fromisoformat(utc_iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def normalize_unix_time(value):
    """Normalize seconds or milliseconds Unix timestamps for playlist ordering."""
    try:
        timestamp = float(value)
        return timestamp / 1000 if timestamp > 100_000_000_000 else timestamp
    except (TypeError, ValueError):
        return None


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
            stream_url, quality = select_chuoi_stream(streams)
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
                "referer": CHUOI_STREAM_REFERER,
                "origin": CHUOI_STREAM_ORIGIN,
                "start_time": unix_time_from_iso(match_time),
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
            record_source_health("ColaTV API", True)
            return list(matches.values())
    except Exception as e:
        record_source_health("ColaTV API", False, str(e))
        print(f"[-] [ColaTV] Lỗi khi gọi API: {e}", file=sys.stderr)
        return []


def _cola_select_best_variant(master_url):
    """
    video_url trả về của Cola thường là master playlist HLS multi-bitrate
    (có sẵn 1080p/720p/540p/360p), nhưng nhiều player (kể cả TiviMate) không
    tự chọn ABR mà chỉ phát đúng luồng đầu tiên liệt kê trong file - thường
    lại là bản thấp nhất. Nên tự tải master playlist, tìm bản RESOLUTION cao
    nhất và trả thẳng URL của bản đó.
    """
    try:
        req = urllib.request.Request(master_url)
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Referer", COLA_REFERER)
        with urllib.request.urlopen(req, timeout=10) as resp:
            playlist = resp.read().decode("utf-8", "ignore")
    except Exception:
        return master_url

    best_pixels = -1
    best_uri = None
    lines = playlist.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF"):
            continue
        m = re.search(r'RESOLUTION=(\d+)x(\d+)', line)
        if not m or i + 1 >= len(lines):
            continue
        pixels = int(m.group(1)) * int(m.group(2))
        uri = lines[i + 1].strip()
        if uri and pixels > best_pixels:
            best_pixels = pixels
            best_uri = uri

    if not best_uri:
        return master_url

    return urllib.parse.urljoin(master_url, best_uri)


def build_channels_cola(matches):
    """Chuẩn hóa dữ liệu trận đấu từ Cola TV thành danh sách channel chung"""
    channels = []
    for match in matches:
        stream_url = match.get("video_url") or match.get("videoUrl")
        # API trả "https" (placeholder) khi trận chưa mở luồng - bỏ qua
        if not stream_url or not stream_url.startswith("http") or ".m3u8" not in stream_url:
            continue

        stream_url = _cola_select_best_variant(stream_url)

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
            "start_time": normalize_unix_time(match_time),
        })

    return channels


# ---------------------------------------------------------------------------
# Xoilac TV
# ---------------------------------------------------------------------------
XOILAC_SCHEDULE_URL = "https://data-api.sportflowlivez.com/v1/football/xoilac365/match/live"
XOILAC_MATCH_DETAIL_URL = "https://fb-api.sportliveapiz.com/football/match/{}"
XOILAC_SITE_URL = "https://xoilacxbb.tv"
XOILAC_REFERER = "https://xoilacxbb.tv/"
XOILAC_ORIGIN = "https://xoilacxbb.tv"
XOILAC_SOURCE_TAG = "XoilacTV"
# 1=chưa đá, 8=đã kết thúc, còn lại là các trạng thái đang diễn ra (hiệp 1/hiệp 2/nghỉ...)
# Riêng 9 không phải trạng thái live thật (dữ liệu rác/trận có giờ đá bất thường) nên loại luôn.
XOILAC_LIVE_STATUS = (2, 3, 4, 5, 6, 7)
XOILAC_NOT_STARTED_STATUS = 1
# Mỗi trận Xoilac cần thêm request để lấy thông tin và resolve stream. Chỉ lấy
# lịch sắp diễn ra trong 3 giờ để cron không phải quét hàng trăm trận tương lai.
XOILAC_UPCOMING_WINDOW_SECONDS = 3 * 60 * 60


def _filter_xoilac_matches(matches, now_ts=None):
    """Giữ trận đang LIVE và trận chưa đá bắt đầu trong 3 giờ tới."""
    if now_ts is None:
        now_ts = time.time()

    result = []
    for match in matches:
        status = match.get("status_id")
        if status in XOILAC_LIVE_STATUS:
            result.append(match)
            continue
        if status != XOILAC_NOT_STARTED_STATUS:
            continue

        match_time = normalize_unix_time(match.get("match_time"))
        if (
            match_time is not None
            and 0 <= match_time - now_ts <= XOILAC_UPCOMING_WINDOW_SECONDS
        ):
            result.append(match)
    return result


def _http_get(url, referer=None, timeout=10):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/json, text/html, */*")
    if referer:
        req.add_header("Referer", referer)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def _xoilac_matches_from_homepage():
    """Fallback schedule scraped from Xoilac's public match cards.

    The primary schedule API is occasionally protected by a Cloudflare
    challenge, while the homepage and per-match API remain publicly readable.
    """
    try:
        html = _http_get(f"{XOILAC_SITE_URL}/", referer=XOILAC_REFERER)
    except Exception as e:
        print(f"[-] [XoilacTV] Lỗi fallback trang chủ: {e}", file=sys.stderr)
        return []

    now = datetime.now(timezone(timedelta(hours=7)))
    matches = []
    cards = re.findall(
        r'<a class="[^"]*match-horizontals-item[^"]*"\s+'
        r'href="([^"]+)"\s+id="horizontal-item-([^"]+)">(.*?)</a>',
        html,
        flags=re.DOTALL,
    )
    for href, match_id, card_html in cards:
        time_match = re.search(r'<div class="h-time">\s*(.*?)\s*</div>', card_html, re.DOTALL)
        if not time_match:
            continue
        label = re.sub(r'<[^>]+>', '', time_match.group(1)).strip()
        clock = re.search(r'(\d{1,2}):(\d{2})', label)
        if not clock:
            continue

        match_date = now.date()
        label_lower = label.lower()
        if "ngày mai" in label_lower:
            match_date += timedelta(days=1)
        elif "hôm qua" in label_lower:
            match_date -= timedelta(days=1)
        match_time = datetime(
            match_date.year,
            match_date.month,
            match_date.day,
            int(clock.group(1)),
            int(clock.group(2)),
            tzinfo=now.tzinfo,
        ).timestamp()

        is_live = "trực tiếp" in label_lower or "live" in label_lower
        matches.append({
            "id": match_id,
            "slug": urllib.parse.urljoin(XOILAC_SITE_URL, href)
            .replace(XOILAC_SITE_URL, "")
            .rstrip("/"),
            "match_time": match_time,
            "status_id": 2 if is_live else XOILAC_NOT_STARTED_STATUS,
        })
    return _filter_xoilac_matches(matches, now.timestamp())


def _hls_get(url, referer, origin, timeout=10):
    """Fetch an HLS playlist with the same headers supplied to the player."""
    req = urllib.request.Request(url)
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/vnd.apple.mpegurl, application/x-mpegURL, */*")
    if referer:
        req.add_header("Referer", referer)
    if origin:
        req.add_header("Origin", origin)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.geturl(), resp.read().decode("utf-8", "ignore")


def _hls_first_uri(playlist, marker):
    """Return the first non-comment URI after a particular HLS tag."""
    lines = playlist.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(marker):
            for candidate in lines[index + 1:]:
                candidate = candidate.strip()
                if candidate and not candidate.startswith("#"):
                    return candidate
    return None


def _hls_is_playable(stream_url, referer, origin):
    """
    Confirm that a stream can return a media playlist and a media segment.

    Some providers return HTTP 200 for a landing page or an empty/master
    playlist. Checking the first segment prevents publishing those dead links.
    """
    try:
        playlist_url, playlist = _hls_get(stream_url, referer, origin)
        if "#EXTM3U" not in playlist:
            return False, "response is not an HLS playlist"

        if "#EXT-X-STREAM-INF" in playlist:
            variant_uri = _hls_first_uri(playlist, "#EXT-X-STREAM-INF")
            if not variant_uri:
                return False, "master playlist has no variant"
            playlist_url, playlist = _hls_get(
                urllib.parse.urljoin(playlist_url, variant_uri), referer, origin
            )

        segment_uri = _hls_first_uri(playlist, "#EXTINF")
        if not segment_uri:
            return False, "playlist has no media segment"

        segment_req = urllib.request.Request(
            urllib.parse.urljoin(playlist_url, segment_uri)
        )
        segment_req.add_header("User-Agent", USER_AGENT)
        segment_req.add_header("Referer", referer)
        segment_req.add_header("Origin", origin)
        segment_req.add_header("Range", "bytes=0-1")
        with urllib.request.urlopen(segment_req, timeout=10) as resp:
            if not resp.read(1):
                return False, "first media segment is empty"
        return True, None
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, type(e).__name__


def fetch_matches_xoilac():
    """
    Lấy trận đang LIVE và trận sắp diễn ra trong 3 giờ từ Xoilac TV.
    """
    try:
        raw = _http_get(XOILAC_SCHEDULE_URL, referer=XOILAC_REFERER)
        data = json.loads(raw)
        matches = data.get("matches", []) or []
    except Exception as e:
        record_source_health("XoilacTV schedule API", False, str(e))
        print(f"[-] [XoilacTV] Lỗi khi gọi API lịch thi đấu: {e}", file=sys.stderr)
        fallback_matches = _xoilac_matches_from_homepage()
        if fallback_matches:
            print(
                f"[+] [XoilacTV] Dùng fallback trang chủ: "
                f"{len(fallback_matches)} trận.",
                file=sys.stderr,
            )
        return fallback_matches

    record_source_health("XoilacTV schedule API", True)
    return _filter_xoilac_matches(matches)


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
        is_live = match.get("status_id") in XOILAC_LIVE_STATUS
        status_prefix = "● [LIVE] " if is_live else f"[{time_vn}] "

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
            "start_time": normalize_unix_time(match.get("match_time")),
        })

    return channels


# ---------------------------------------------------------------------------
# Xuất file M3U8
# ---------------------------------------------------------------------------

def generate_m3u8(channels, output_file="sport.m3u8"):
    """Tạo file playlist m3u8 từ danh sách channel đã chuẩn hóa (nhiều nguồn)"""
    # Keep current broadcasts first, then put the nearest kickoffs at the top.
    # Sources return different orders, so this must happen after merging them.
    channels = sorted(
        channels,
        key=lambda ch: (
            0 if ch["status_prefix"].startswith("● [LIVE]") else 1,
            ch["start_time"] if ch["start_time"] is not None else float("inf"),
            ch["home"],
            ch["away"],
        ),
    )
    tz_vn = timezone(timedelta(hours=7))
    now_vn = datetime.now(tz_vn).strftime("%H:%M:%S %d/%m/%Y")

    lines = [
        "#EXTM3U x-tvg-url=\"\"",
        "## Playlist Thể Thao Tự Động - Nguồn: Chuối Chiên TV, Cola TV",
        f"## Cập nhật lúc: {now_vn}",
        ""
    ]

    count = 0
    health_cache = {}
    for ch in channels:
        # Validate only on-air channels. Checking every upcoming fixture can
        # mean hundreds of requests and would make a scheduled crawl too slow.
        # ChuốiTV's CDN rejects datacenter IPs (including this VPS) while
        # allowing viewer networks, so a server-side probe would create false
        # negatives and incorrectly remove otherwise playable channels.
        if (
            ch["source_tag"] != "ChuoiTV"
            and ch["status_prefix"].startswith("● [LIVE]")
        ):
            health_key = (ch["stream_url"], ch["referer"], ch["origin"])
            if health_key not in health_cache:
                health_cache[health_key] = _hls_is_playable(*health_key)
            is_playable, reason = health_cache[health_key]
            if not is_playable:
                print(
                    f"[-] Bỏ link LIVE không phát được ({ch['source_tag']} - "
                    f"{ch['home']} vs {ch['away']}): {reason}",
                    file=sys.stderr,
                )
                continue

        tivimate_stream_url = (
            f"{ch['stream_url']}|Referer={ch['referer']}&Origin={ch['origin']}&User-Agent={USER_AGENT}"
        )
        # TiviMate groups entries by an exact group-title. Keep one stable
        # group per provider instead of creating a separate group per league.
        group_title = PROVIDER_GROUPS.get(ch["source_tag"], ch["source_tag"])
        channel_name = (
            f"{ch['status_prefix']}{ch['home']} vs {ch['away']}"
            f"{ch['channel_suffix']} — {ch['league']}"
        )

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
    print(
        f"[+] [XoilacTV] Đã lấy được {len(xoilac_matches)} "
        "trận LIVE/sắp diễn ra trong 3 giờ."
    )
    notify_source_health()
    all_channels.extend(build_channels_xoilac(xoilac_matches))

    if not all_channels:
        print("[-] Không lấy được dữ liệu kênh nào từ các nguồn. Đang thử lại hoặc kết thúc.")
        sys.exit(1)

    generate_m3u8(all_channels, "sport.m3u8")
