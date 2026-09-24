#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sport M3U8 Playlist Crawler
Crawl lịch thi đấu và link stream HLS trực tiếp từ nhiều nguồn: Chuối Chiên TV, Gà Vàng TV, BiaomTV, Cola TV.
Ưu tiên chất lượng: FULL HD -> HD -> SD.
Xuất file sport.m3u8 tương thích 100% với TiviMate, VLC, OTT Navigator.
Mỗi nguồn được gắn tiền tố riêng trong group-title để không bị trộn lẫn.
"""

import re
import sys
import json
import time
import os
import html as html_lib
from concurrent.futures import ThreadPoolExecutor
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
    "GavangTV": "Gà Vàng TV",
    "BiaomTV": "BiaomTV",
    "ColaTV": "Cola TV",
}
PROVIDER_ORDER = {source: index for index, source in enumerate(PROVIDER_GROUPS)}

DEFAULT_LOGO = "https://media.chuoichientv.com/media/uploads/default-thumbnail.png"
SOURCE_HEALTH = {}
SOURCE_HEALTH_STATE_FILE = os.getenv("SOURCE_HEALTH_STATE_FILE", ".source-health.json")
SOURCE_FAILURE_THRESHOLD = max(int(os.getenv("SOURCE_FAILURE_THRESHOLD", "3")), 1)
TRUYEN_HINH_PLAYLIST_URL = "https://tinyurl.com/vietxiaomi"
TRUYEN_HINH_OUTPUT_FILE = "truyenhinh.m3u8"
MAX_TELEVISION_PLAYLIST_BYTES = 20 * 1024 * 1024


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


def sync_television_playlist(
    source_url=TRUYEN_HINH_PLAYLIST_URL,
    output_file=TRUYEN_HINH_OUTPUT_FILE,
):
    """Download a complete television M3U playlist and atomically replace the old file."""
    request = urllib.request.Request(source_url, headers={"User-Agent": USER_AGENT})
    temp_file = f"{output_file}.tmp"
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            chunks = []
            total = 0
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_TELEVISION_PLAYLIST_BYTES:
                    raise ValueError("playlist vượt giới hạn 20 MB")
                chunks.append(chunk)

        content = b"".join(chunks).decode("utf-8-sig")
        if not content.lstrip().startswith("#EXTM3U"):
            raise ValueError("nguồn trả về dữ liệu không phải M3U")
        if "#EXTINF" not in content:
            raise ValueError("playlist không có kênh nào")

        with open(temp_file, "w", encoding="utf-8", newline="\n") as playlist:
            playlist.write(content.rstrip() + "\n")
        os.replace(temp_file, output_file)
        count = content.count("#EXTINF")
        record_source_health("Truyền hình M3U", True)
        print(f"[+] Đã cập nhật '{output_file}' với {count} kênh.")
        return True
    except Exception as error:
        try:
            os.remove(temp_file)
        except OSError:
            pass
        record_source_health("Truyền hình M3U", False, str(error))
        print(f"[-] Không cập nhật được '{output_file}': {error}", file=sys.stderr)
        return False

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


def ordered_chuoi_streams(streams):
    """Return usable ChuoiTV streams ordered from best to lowest quality."""
    candidates = []
    for index, stream in enumerate(streams or []):
        url = stream.get("url") or stream.get("streamUrl")
        if not url:
            continue
        label = (stream.get("label") or "SD").upper().strip()
        if "4K" in label or "2160" in label:
            rank = 0
        elif "FHD" in label or "FULL HD" in label or "1080" in label:
            rank = 1
        elif "HD" in label or "720" in label:
            rank = 2
        else:
            rank = 3
        candidates.append((rank, index, url, label))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return [(url, label) for _, _, url, label in candidates]


def select_chuoi_stream(streams):
    """Choose the highest-quality ChuoiTV stream available from the API."""
    candidates = ordered_chuoi_streams(streams)
    return candidates[0] if candidates else (None, None)


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


# A source's own "live" flag (API status field or an HTML badge class) isn't
# cross-checked against the fixture's scheduled kickoff anywhere upstream, so
# a wrong/stale flag — live before kickoff, or still live hours after a match
# that's long over — gets trusted as-is. The HLS reachability probe in
# generate_m3u8() can't catch this either: a dead/placeholder stream can still
# answer with a valid manifest and a first segment. Gate every source's flag
# through this window instead.
LIVE_FLAG_PRE_ROLL_SECONDS = 15 * 60
LIVE_FLAG_WINDOW_SECONDS = 3 * 60 * 60


def _within_live_window(start_time, now=None):
    """True if start_time plausibly places a match inside its live window.

    No start_time to check against (source doesn't provide one) → trust the
    source's own flag as-is rather than discard it.
    """
    if start_time is None:
        return True
    now = time.time() if now is None else now
    return start_time - LIVE_FLAG_PRE_ROLL_SECONDS <= now < start_time + LIVE_FLAG_WINDOW_SECONDS


def build_channels_chuoi(matches):
    """Chuẩn hóa dữ liệu trận đấu từ Chuối Chiên TV thành danh sách channel chung"""
    channels = []
    for match in matches:
        home = match.get("teams", {}).get("home", {}).get("name", "Đội nhà")
        away = match.get("teams", {}).get("away", {}).get("name", "Đội khách")
        home_logo = match.get("teams", {}).get("home", {}).get("logo", "")
        away_logo = match.get("teams", {}).get("away", {}).get("logo", "")

        league = match.get("league", {}).get("name", "Bóng Đá")
        league_logo = match.get("league", {}).get("logo", "")
        logo = home_logo or league_logo or DEFAULT_LOGO

        match_time = match.get("matchTime", "")
        time_vn = format_time_vn_iso(match_time)
        start_time = unix_time_from_iso(match_time)
        live = match.get("status", "") == "live" and _within_live_window(start_time)
        status_prefix = "● [LIVE] " if live else f"[{time_vn}] "

        blvs = match.get("blvs", []) or []
        if not blvs:
            blvs = match.get("blvs_bonglau", []) or match.get("blvs_nguoitho", []) or []
        if not blvs:
            continue

        for blv in blvs:
            blv_name = blv.get("name") or "BLV"
            streams = blv.get("streams") or []
            candidates = ordered_chuoi_streams(streams)
            if not candidates:
                continue
            stream_url, quality = candidates[0]
            if not stream_url:
                continue

            tvg_id = f"cctv_{match.get('externalId', 'x')}_{blv.get('username', 'blv')}"
            channels.append({
                "source_tag": CHUOI_SOURCE_TAG,
                "status_prefix": status_prefix,
                "home": home,
                "away": away,
                "logo": logo,
                "home_logo": home_logo or logo,
                "away_logo": away_logo,
                "league": league,
                "channel_suffix": f" - {blv_name} [{quality}]",
                "blv_name": blv_name,
                "stream_candidates": candidates,
                "tvg_id": tvg_id,
                "stream_url": stream_url,
                "referer": CHUOI_STREAM_REFERER,
                "origin": CHUOI_STREAM_ORIGIN,
                "start_time": start_time,
            })

    return channels


# ---------------------------------------------------------------------------
# Cola TV
# ---------------------------------------------------------------------------
COLA_API_URL = "https://api.gvapi.cc/api/matches"
COLA_REFERER = "https://colatvttbdh.tv/"
COLA_ORIGIN = "https://colatvttbdh.tv"
COLA_SOURCE_TAG = "ColaTV"
COLA_UPCOMING_WINDOW_SECONDS = 3 * 60 * 60
COLA_ESTIMATED_LIVE_SECONDS = 3 * 60 * 60


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


def _cola_anchor_stream_candidates(match):
    """Return active commentator HLS streams supplied by Cola's own API."""
    match_id = match.get("match_id") or match.get("matchId")
    candidates = []
    for anchor in match.get("anchorAppointmentVoList") or []:
        anchor_match_id = anchor.get("matchId")
        if match_id and anchor_match_id and anchor_match_id != match_id:
            continue
        if anchor.get("liveStatus") not in (2, "2", "live"):
            continue

        urls = [anchor.get("playStreamAddress2"), *(anchor.get("servers") or [])]
        for url in urls:
            if url and url.startswith("http") and ".m3u8" in url:
                candidates.append((url, anchor.get("nickName") or "BLV"))
    return candidates


def _cola_select_stream(match, primary_stream, status):
    """Prefer Cola's TiviMate-compatible commentator stream when live."""
    if status == "live":
        for stream_url, anchor_name in _cola_anchor_stream_candidates(match):
            playable, _ = _hls_is_playable(stream_url, COLA_REFERER, COLA_ORIGIN)
            if playable:
                return stream_url, f" - {anchor_name}"

    return _cola_select_best_variant(primary_stream), ""


def build_channels_cola(matches):
    """Chuẩn hóa dữ liệu trận đấu từ Cola TV thành danh sách channel chung"""
    channels = []
    now = time.time()
    for match in matches:
        primary_stream = match.get("video_url") or match.get("videoUrl")
        # API trả "https" (placeholder) khi trận chưa mở luồng - bỏ qua
        if (
            not primary_stream
            or not primary_stream.startswith("http")
            or ".m3u8" not in primary_stream
        ):
            continue

        status = match.get("match_status") or match.get("matchStatus")
        match_time = match.get("match_time") or match.get("matchTime")
        start_time = normalize_unix_time(match_time)
        source_live = status == "live" and _within_live_window(start_time, now)
        estimated_live = (
            start_time is not None
            and start_time <= now < start_time + COLA_ESTIMATED_LIVE_SECONDS
        )
        upcoming = (
            start_time is not None
            and now < start_time <= now + COLA_UPCOMING_WINDOW_SECONDS
        )
        # Cola's API frequently keeps historical fixtures with an expired
        # video_url. Do not expose them as playable cards in the app.
        if not source_live and not estimated_live and not upcoming:
            continue
        stream_url, channel_suffix = _cola_select_stream(
            match, primary_stream, status
        )

        home_team = match.get("home_team") or {}
        away_team = match.get("away_team") or {}
        competition = match.get("competition") or {}

        home = home_team.get("name") or match.get("homeTeamName") or "Đội nhà"
        away = away_team.get("name") or match.get("awayTeamName") or "Đội khách"
        home_logo = home_team.get("logo") or competition.get("logo") or DEFAULT_LOGO
        away_logo = away_team.get("logo") or ""
        logo = home_logo
        league = competition.get("name") or match.get("competitionName") or "Bóng Đá"

        time_vn = format_time_vn_unix(match_time)
        status_prefix = "● [LIVE] " if source_live or estimated_live else f"[{time_vn}] "

        match_id = match.get("match_id") or match.get("matchId") or "x"
        channels.append({
            "source_tag": COLA_SOURCE_TAG,
            "status_prefix": status_prefix,
            "home": home,
            "away": away,
            "logo": logo,
            "home_logo": home_logo,
            "away_logo": away_logo,
            "league": league,
            "channel_suffix": channel_suffix,
            "tvg_id": f"cola_{match_id}",
            "stream_url": stream_url,
            "referer": COLA_REFERER,
            "origin": COLA_ORIGIN,
            "start_time": start_time,
        })

    return channels


# ---------------------------------------------------------------------------
# HTTP / HLS helpers shared by providers
# ---------------------------------------------------------------------------
def _http_get(url, referer=None, origin=None, extra_headers=None, timeout=10):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/json, text/html, */*")
    if referer:
        req.add_header("Referer", referer)
    if origin:
        req.add_header("Origin", origin)
    for name, value in (extra_headers or {}).items():
        req.add_header(name, value)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def _hls_get(url, referer, origin, timeout=10):
    """Fetch an HLS playlist using the same headers as the player."""
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


def _hls_is_playable(stream_url, referer, origin, timeout=5):
    """Confirm an HLS playlist and segment, within roughly 15 seconds total."""
    try:
        playlist_url, playlist = _hls_get(stream_url, referer, origin, timeout)
        if "#EXTM3U" not in playlist:
            return False, "response is not an HLS playlist"
        if "#EXT-X-STREAM-INF" in playlist:
            variant_uri = _hls_first_uri(playlist, "#EXT-X-STREAM-INF")
            if not variant_uri:
                return False, "master playlist has no variant"
            playlist_url, playlist = _hls_get(
                urllib.parse.urljoin(playlist_url, variant_uri), referer, origin, timeout
            )
        segment_uri = _hls_first_uri(playlist, "#EXTINF")
        if not segment_uri:
            return False, "playlist has no media segment"
        segment_req = urllib.request.Request(urllib.parse.urljoin(playlist_url, segment_uri))
        segment_req.add_header("User-Agent", USER_AGENT)
        segment_req.add_header("Referer", referer)
        segment_req.add_header("Origin", origin)
        segment_req.add_header("Range", "bytes=0-1")
        with urllib.request.urlopen(segment_req, timeout=timeout) as resp:
            if not resp.read(1):
                return False, "first media segment is empty"
        return True, None
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, type(e).__name__


# ---------------------------------------------------------------------------
# Gà Vàng TV
# ---------------------------------------------------------------------------
# Gà Vàng renders the fixture cards and player URLs in the server response, so
# urllib is sufficient here.  Keeping it dependency-free is important for the
# VPS cron job; Playwright is not required.
GAVANG_SITE_URL = "https://gavanglinkm.tv"
GAVANG_REFERER = f"{GAVANG_SITE_URL}/"
GAVANG_ORIGIN = GAVANG_SITE_URL
GAVANG_SOURCE_TAG = "GavangTV"
GAVANG_UPCOMING_WINDOW_SECONDS = 3 * 60 * 60


def _gavang_text(value):
    """Turn a small HTML fragment into clean, decoded display text."""
    return html_lib.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()


def _gavang_image_urls(card_html):
    """Return the two team crests in their home/away card order."""
    images = re.findall(
        r'<img\s+[^>]*data-src="([^"]+)"[^>]*alt="[^"]*Logo"',
        card_html,
        flags=re.IGNORECASE,
    )
    return [html_lib.unescape(url) for url in images[:2]]


def fetch_matches_gavang():
    """Read live and near-term fixtures from Gà Vàng's public home page."""
    try:
        page = _http_get(
            GAVANG_REFERER, referer=GAVANG_REFERER, origin=GAVANG_ORIGIN
        )
    except Exception as e:
        record_source_health("Gà Vàng TV schedule", False, str(e))
        print(f"[-] [GavangTV] Lỗi tải trang chủ: {e}", file=sys.stderr)
        return []

    # Each card begins with this marker.  Splitting avoids brittle nested-div
    # regular expressions while retaining all fields until the next card.
    card_parts = re.split(r'<div\s+class="match-card\b', page, flags=re.I)
    now_ts = time.time()
    matches = []
    seen = set()
    for part in card_parts[1:]:
        card_html = part.split('<div class="match-card', 1)[0]
        match_id = re.search(r'data-match-id="([^"]+)"', card_html)
        timestamp = re.search(r'data-match-time="(\d+)"', card_html)
        link = re.search(r'href="([^"]+/truc-tiep/[^"]+)"', card_html)
        home = re.search(
            r'class="[^"]*bals-home-team-name[^"]*"[^>]*>\s*(.*?)\s*</div>',
            card_html,
            re.S,
        )
        away = re.search(
            r'class="[^"]*bals-away-team-name[^"]*"[^>]*>\s*(.*?)\s*</div>',
            card_html,
            re.S,
        )
        if not all((match_id, timestamp, link, home, away)):
            continue

        start_time = normalize_unix_time(timestamp.group(1))
        is_live = "bals-live-match" in card_html[:1000] and _within_live_window(start_time, now_ts)
        if not is_live and (
            start_time is None
            or not 0 <= start_time - now_ts <= GAVANG_UPCOMING_WINDOW_SECONDS
        ):
            continue
        if match_id.group(1) in seen:
            continue
        seen.add(match_id.group(1))

        league = re.search(
            r'class="[^"]*bals-competition-name[^"]*"[^>]*>\s*(.*?)\s*</',
            card_html,
            re.S,
        )
        images = _gavang_image_urls(card_html)
        matches.append({
            "id": match_id.group(1),
            "page_url": urllib.parse.urljoin(GAVANG_SITE_URL, html_lib.unescape(link.group(1))),
            "home": _gavang_text(home.group(1)),
            "away": _gavang_text(away.group(1)),
            "home_logo": images[0] if images else DEFAULT_LOGO,
            "away_logo": images[1] if len(images) > 1 else "",
            "league": _gavang_text(league.group(1)) if league else "Bóng Đá",
            "start_time": start_time,
            "is_live": is_live,
        })

    record_source_health("Gà Vàng TV schedule", True)
    return matches


def _gavang_resolve_stream(match_page_url):
    """Extract the first HLS player option embedded in a public match page.

    Returns (stream_url, stream_name, error_detail). The caller aggregates
    error_detail across every match into one group-level health record
    instead of reporting per-link, since matches share the same source key.
    """
    try:
        page = _http_get(
            match_page_url,
            referer=GAVANG_REFERER,
            origin=GAVANG_ORIGIN,
            # A slow fixture must not hold up the scheduled playlist update.
            # Twelve lookups run in parallel below. Give a temporarily slow
            # player page up to 15 seconds, without blocking the whole crawl.
            timeout=15,
        )
    except Exception as e:
        return None, None, str(e)

    candidates = re.findall(
        r'data-stream-url="(https?[^\"]+?\.m3u8[^\"]*)"[^>]*data-stream-name="([^"]*)"',
        page,
        flags=re.I,
    )
    if not candidates:
        return None, None, "no HLS player URL"

    stream_url, stream_name = candidates[0]
    return html_lib.unescape(stream_url), _gavang_text(stream_name), None


def build_channels_gavang(matches):
    """Normalize public Gà Vàng fixtures into the common channel model."""
    channels = []
    # Resolving one page per fixture serially makes the cron job needlessly
    # slow.  These are independent public requests, so keep the concurrency
    # modest while preserving the source order in the generated playlist.
    with ThreadPoolExecutor(max_workers=12) as pool:
        resolved_streams = list(
            pool.map(lambda match: _gavang_resolve_stream(match["page_url"]), matches)
        )

    failures = [
        f"{match['home']} vs {match['away']}: {error}"
        for match, (_, _, error) in zip(matches, resolved_streams)
        if error
    ]
    if matches:
        if failures:
            record_source_health(
                "Gà Vàng TV stream resolver",
                False,
                f"{len(failures)}/{len(matches)} link lỗi — " + "; ".join(failures),
            )
        else:
            record_source_health("Gà Vàng TV stream resolver", True)

    for match, (stream_url, stream_name, _) in zip(matches, resolved_streams):
        if not stream_url:
            continue
        time_vn = format_time_vn_unix(match["start_time"])
        channels.append({
            "source_tag": GAVANG_SOURCE_TAG,
            "status_prefix": "● [LIVE] " if match["is_live"] else f"[{time_vn}] ",
            "home": match["home"] or "Đội nhà",
            "away": match["away"] or "Đội khách",
            "logo": match["home_logo"],
            "home_logo": match["home_logo"],
            "away_logo": match["away_logo"],
            "league": match["league"],
            "channel_suffix": f" - {stream_name}" if stream_name else "",
            "tvg_id": f"gavang_{match['id']}",
            "stream_url": stream_url,
            "referer": GAVANG_REFERER,
            "origin": GAVANG_ORIGIN,
            "start_time": match["start_time"],
        })
    return channels


# ---------------------------------------------------------------------------
# BiaomTV
# ---------------------------------------------------------------------------
# BiaomTV's old domain (biaomtv.link) and WordPress JSON API are gone; the
# site was rebuilt on Next.js at biaomtv18.com. There is no separate JSON API
# anymore — the /live page is server-rendered with the fixture list (including
# the resolved HLS URLs) embedded inline as a React Server Component payload,
# so we extract it from the HTML instead.
BIAOM_SITE_URL = "https://biaomtv18.com"
BIAOM_REFERER = f"{BIAOM_SITE_URL}/"
BIAOM_ORIGIN = BIAOM_SITE_URL
BIAOM_LIVE_PAGE_URL = f"{BIAOM_SITE_URL}/live"
BIAOM_SOURCE_TAG = "BiaomTV"
_BIAOM_RSC_CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', re.S)


def _biaom_extract_json_array(text, key):
    """Bracket-match the `"<key>":[...]` array value inside an RSC text chunk."""
    marker = f'"{key}":['
    idx = text.find(marker)
    if idx == -1:
        return None
    start = idx + len(marker) - 1  # index of the opening '['
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _biaom_iso_time(value):
    """Convert BiaomTV's ISO-8601 (UTC) fixture time to a unix timestamp."""
    try:
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError):
        return None


def fetch_matches_biaom():
    """Fetch live/upcoming fixtures embedded in BiaomTV's server-rendered /live page."""
    try:
        html = _http_get(
            BIAOM_LIVE_PAGE_URL, referer=BIAOM_REFERER, origin=BIAOM_ORIGIN, timeout=15
        )
    except Exception as e:
        record_source_health("BiaomTV live page", False, str(e))
        print(f"[-] [BiaomTV] Lỗi gọi API lịch thi đấu: {e}", file=sys.stderr)
        return []

    matches = []
    for chunk in _BIAOM_RSC_CHUNK_RE.findall(html):
        if "initialData" not in chunk:
            continue
        try:
            text = json.loads('"' + chunk + '"')  # un-escape the JS string literal
        except (ValueError, json.JSONDecodeError):
            continue
        array_text = _biaom_extract_json_array(text, "initialData")
        if not array_text:
            continue
        try:
            data = json.loads(array_text)
        except (ValueError, json.JSONDecodeError):
            continue
        matches.extend(item for item in data if isinstance(item, dict) and item.get("matchId"))

    if not matches:
        record_source_health("BiaomTV live page", False, "no matches found in page payload")
        return []
    record_source_health("BiaomTV live page", True)
    return matches


def build_channels_biaom(matches):
    """Map BiaomTV's embedded fixture payloads into playable channels."""
    channels = []
    now = time.time()
    for fixture in matches:
        stream_url = fixture.get("mobileStreamUrl")
        if not str(stream_url or "").startswith("http"):
            continue
        match = fixture.get("match") or {}
        home = match.get("home") or {}
        away = match.get("away") or {}
        league = match.get("league") or {}
        start_time = _biaom_iso_time(match.get("starting_at"))
        live = start_time is not None and _within_live_window(start_time, now)
        commentator = (fixture.get("streamer") or {}).get("displayName") or ""
        home_logo = home.get("image_url") or DEFAULT_LOGO
        channels.append({
            "source_tag": BIAOM_SOURCE_TAG,
            "status_prefix": "● [LIVE] " if live else f"[{format_time_vn_unix(start_time)}] ",
            "home": home.get("name") or "Đội nhà",
            "away": away.get("name") or "Đội khách",
            "logo": home_logo,
            "home_logo": home_logo,
            "away_logo": away.get("image_url") or "",
            "league": league.get("name") or "Bóng Đá",
            "channel_suffix": f" - {commentator}" if commentator else "",
            "tvg_id": f"biaom_{fixture.get('matchId') or fixture.get('id')}",
            "stream_url": stream_url,
            "referer": BIAOM_REFERER,
            "origin": BIAOM_ORIGIN,
            "start_time": start_time,
        })
    return channels


# ---------------------------------------------------------------------------
# Xuất file M3U8
# ---------------------------------------------------------------------------

def generate_m3u8(channels, output_file="sport.m3u8"):
    """Tạo file playlist m3u8 từ danh sách channel đã chuẩn hóa (nhiều nguồn)"""
    # Keep provider groups in the requested stable order. Within each group,
    # show current broadcasts before the nearest kickoffs.
    channels = sorted(
        channels,
        key=lambda ch: (
            PROVIDER_ORDER.get(ch["source_tag"], len(PROVIDER_ORDER)),
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
        "## Playlist Thể Thao Tự Động - Nguồn: Chuối Chiên TV, Gà Vàng TV, BiaomTV, Cola TV",
        f"## Cập nhật lúc: {now_vn}",
        ""
    ]

    count = 0
    health_cache = {}
    for ch in channels:
        # Validate only on-air channels. Checking every upcoming fixture can
        # mean hundreds of requests and would make a scheduled crawl too slow.
        if ch["status_prefix"].startswith("● [LIVE]"):
            # Every source's page/API can retain an expired or dead stream_url
            # even while it still reports the match as live — BiaomTV included
            # (its mobileStreamUrl is read straight from the fixture list, same
            # as Gà Vàng's embedded player URL, not independently re-verified).
            # ChuoiTV exposes several qualities for the same commentary.
            # Prefer the highest stream, but fall through to the next one if
            # the CDN rejects it or its HLS playlist is dead.
            if ch["source_tag"] == CHUOI_SOURCE_TAG and ch.get("stream_candidates"):
                playable_candidate = None
                for stream_url, quality in ch["stream_candidates"]:
                    health_key = (stream_url, ch["referer"], ch["origin"])
                    if health_key not in health_cache:
                        health_cache[health_key] = _hls_is_playable(*health_key)
                    is_playable, reason = health_cache[health_key]
                    if is_playable:
                        playable_candidate = (stream_url, quality)
                        break
                if not playable_candidate:
                    print(
                        f"[-] Bỏ link LIVE không phát được ({ch['source_tag']} - "
                        f"{ch['home']} vs {ch['away']}): {reason}",
                        file=sys.stderr,
                    )
                    continue
                ch["stream_url"], quality = playable_candidate
                ch["channel_suffix"] = f" - {ch['blv_name']} [{quality}]"
            else:
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

        # TiviMate versions differ in how they read per-channel HTTP headers.
        # ChuoiTV only needs Referer, so emit both URL-pipe and #EXTHTTP forms
        # for it and omit Origin. Other providers use the standard HLS headers.
        ext_http_line = None
        if ch["source_tag"] == CHUOI_SOURCE_TAG:
            stream_headers = {
                "Referer": ch["referer"],
                "User-Agent": USER_AGENT,
            }
            pipe_headers = "&".join(
                f"{name}={value}" for name, value in stream_headers.items()
            )
            ext_http_line = "#EXTHTTP:" + json.dumps(
                stream_headers, ensure_ascii=False, separators=(",", ":")
            )
        else:
            pipe_headers = (
                f"Referer={ch['referer']}&Origin={ch['origin']}"
                f"&User-Agent={USER_AGENT}"
            )
        tivimate_stream_url = f"{ch['stream_url']}|{pipe_headers}"
        # TiviMate groups entries by an exact group-title. Keep one stable
        # group per provider instead of creating a separate group per league.
        group_title = PROVIDER_GROUPS.get(ch["source_tag"], ch["source_tag"])
        # Keep the M3U display name clean. The Android app reads start-time
        # below and decides locally whether to render a LIVE badge or kickoff
        # time, rather than relying on a stale crawler-generated prefix.
        channel_name = (
            f"{ch['home']} vs {ch['away']}"
            f"{ch['channel_suffix']} — {ch['league']}"
        )
        start_time = int(ch["start_time"] or 0)

        lines.append(
            f'#EXTINF:-1 tvg-id="{ch["tvg_id"]}" tvg-name="{ch["home"]} vs {ch["away"]}" '
            f'tvg-logo="{ch["logo"]}" home-logo="{ch.get("home_logo", ch["logo"])}" '
            f'away-logo="{ch.get("away_logo", "")}" group-title="{group_title}" '
            f'start-time="{start_time}",{channel_name}'
        )
        lines.append(f"#EXTVLCOPT:http-referrer={ch['referer']}")
        lines.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
        if ext_http_line:
            lines.append(ext_http_line)
        lines.append(tivimate_stream_url)
        lines.append("")
        count += 1

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"[+] Đã tạo thành công file '{output_file}' với {count} kênh/luồng stream!")


if __name__ == "__main__":
    all_channels = []

    print("[*] Đang tải playlist Truyền hình...")
    sync_television_playlist()

    print("[*] Đang tải lịch thi đấu từ Chuối Chiên TV...")
    chuoi_matches = fetch_matches_chuoi()
    print(f"[+] [ChuoiTV] Đã lấy được {len(chuoi_matches)} trận đấu.")
    all_channels.extend(build_channels_chuoi(chuoi_matches))

    print("[*] Đang tải lịch thi đấu từ Gà Vàng TV...")
    gavang_matches = fetch_matches_gavang()
    print(
        f"[+] [GavangTV] Đã lấy được {len(gavang_matches)} "
        "trận LIVE/sắp diễn ra trong 3 giờ."
    )
    all_channels.extend(build_channels_gavang(gavang_matches))

    print("[*] Đang tải lịch thi đấu từ BiaomTV...")
    biaom_matches = fetch_matches_biaom()
    print(f"[+] [BiaomTV] Đã lấy được {len(biaom_matches)} trận đấu đang bật.")
    all_channels.extend(build_channels_biaom(biaom_matches))

    print("[*] Đang tải lịch thi đấu từ Cola TV...")
    cola_matches = fetch_matches_cola()
    print(f"[+] [ColaTV] Đã lấy được {len(cola_matches)} trận đấu.")
    all_channels.extend(build_channels_cola(cola_matches))

    # Run health notification only after all source and stream checks finish.
    notify_source_health()

    if not all_channels:
        print("[-] Không lấy được dữ liệu bóng đá. Giữ nguyên sport.m3u8 hiện tại.")
    else:
        generate_m3u8(all_channels, "sport.m3u8")
