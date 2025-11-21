import math
import time
from typing import Dict, Any, List

import requests
import streamlit as st

from config import (
    OPENWEATHER_API_KEY,
    OPENWEATHER_USE_ONECALL,
    OPENWEATHER_UNITS,
    OPENWEATHER_LANG,
    CACHE_TTL,
    ENABLE_WEATHER_TILES,
    WEATHER_TILE_OPACITY,
)

ONECALL_URL = "https://api.openweathermap.org/data/3.0/onecall"
CURRENT_URL  = "https://api.openweathermap.org/data/2.5/weather"
FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"

class WeatherError(Exception):
    pass

def _ensure_api_key():
    if not OPENWEATHER_API_KEY:
        raise WeatherError("Chưa cấu hình OPENWEATHER_API_KEY. Hãy tạo file .env và điền API key.")

def _to_local_ts(ts_utc: int, tz_offset: int) -> int:
    # Trả về timestamp 'giả địa phương' (UTC + offset) cho hiển thị
    return ts_utc + tz_offset

def _kelvin_to_c(k: float) -> float:
    return k - 273.15

def _pick(obj: Dict[str, Any], *keys) -> Dict[str, Any]:
    return {k: obj.get(k) for k in keys}

def _http_get(url: str, params: Dict[str, Any], timeout=20) -> Dict[str, Any]:
    # Retry nhẹ nhàng
    last_err = None
    for _ in range(3):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            # nếu rate limit hay 401 > trả lỗi để caller fallback
            last_err = WeatherError(f"HTTP {r.status_code}: {r.text[:200]}")
        except Exception as e:
            last_err = e
        time.sleep(0.6)
    raise last_err if last_err else WeatherError("Lỗi kết nối OpenWeather")

@st.cache_data(show_spinner=False, ttl=CACHE_TTL)
def _fetch_onecall(lat: float, lon: float, units: str, lang: str) -> Dict[str, Any]:
    params = {
        "lat": lat, "lon": lon,
        "units": units, "lang": lang,
        "appid": OPENWEATHER_API_KEY,
        "exclude": "minutely,alerts",
    }
    return _http_get(ONECALL_URL, params)

@st.cache_data(show_spinner=False, ttl=CACHE_TTL)
def _fetch_current(lat: float, lon: float, units: str, lang: str) -> Dict[str, Any]:
    params = {"lat": lat, "lon": lon, "units": units, "lang": lang, "appid": OPENWEATHER_API_KEY}
    return _http_get(CURRENT_URL, params)

@st.cache_data(show_spinner=False, ttl=CACHE_TTL)
def _fetch_forecast(lat: float, lon: float, units: str, lang: str) -> Dict[str, Any]:
    params = {"lat": lat, "lon": lon, "units": units, "lang": lang, "appid": OPENWEATHER_API_KEY}
    return _http_get(FORECAST_URL, params)

def _normalize_onecall(payload: Dict[str, Any]) -> Dict[str, Any]:
    tz_offset = int(payload.get("timezone_offset", 0))
    current = payload.get("current", {})
    hourly = payload.get("hourly", [])[:24]
    daily  = payload.get("daily", [])[:8]

    def norm_current(c):
        w = (c.get("weather") or [{}])[0]
        return {
            "dt_local": _to_local_ts(int(c["dt"]), tz_offset),
            "temp": c.get("temp"),
            "feels_like": c.get("feels_like"),
            "humidity": c.get("humidity"),
            "wind_speed": c.get("wind_speed"),
            "wind_deg": c.get("wind_deg"),
            "uvi": c.get("uvi"),
            "pressure": c.get("pressure"),
            "clouds": c.get("clouds"),
            "pop": c.get("pop", 0),
            "desc": w.get("description"),
            "icon": w.get("icon"),
        }

    def norm_hour(h):
        w = (h.get("weather") or [{}])[0]
        return {
            "dt_local": _to_local_ts(int(h["dt"]), tz_offset),
            "temp": h.get("temp"),
            "pop": h.get("pop", 0),
            "humidity": h.get("humidity"),
            "wind_speed": h.get("wind_speed"),
            "desc": w.get("description"),
            "icon": w.get("icon"),
        }

    def norm_day(d):
        w = (d.get("weather") or [{}])[0]
        temps = d.get("temp") or {}
        return {
            "dt_local": _to_local_ts(int(d["dt"]), tz_offset),
            "t_min": temps.get("min"),
            "t_max": temps.get("max"),
            "pop": d.get("pop", 0),
            "humidity": d.get("humidity"),
            "wind_speed": d.get("wind_speed"),
            "desc": w.get("description"),
            "icon": w.get("icon"),
        }

    return {
        "source": "onecall_3_0",
        "tz_offset": tz_offset,
        "current": norm_current(current) if current else None,
        "hourly": [norm_hour(h) for h in hourly],
        "daily":  [norm_day(d) for d in daily],
    }

def _normalize_from_current_forecast(cur: Dict[str, Any], fc: Dict[str, Any]) -> Dict[str, Any]:
    # tz offset: lấy từ cur.timezone hoặc tính gần đúng theo lon (~ 4 phút/độ)
    tz_offset = int(cur.get("timezone", 0))
    if tz_offset == 0:
        tz_offset = int(round((cur.get("coord", {}).get("lon", 0)) * 240))  # 1 độ ~ 240s

    cw = (cur.get("weather") or [{}])[0]
    current = {
        "dt_local": _to_local_ts(int(cur.get("dt", 0)), tz_offset),
        "temp": (cur.get("main") or {}).get("temp"),
        "feels_like": (cur.get("main") or {}).get("feels_like"),
        "humidity": (cur.get("main") or {}).get("humidity"),
        "wind_speed": (cur.get("wind") or {}).get("speed"),
        "wind_deg": (cur.get("wind") or {}).get("deg"),
        "pressure": (cur.get("main") or {}).get("pressure"),
        "clouds": (cur.get("clouds") or {}).get("all"),
        "desc": cw.get("description"),
        "icon": cw.get("icon"),
        "pop": 0,
    }

    # 5 ngày / 3 giờ → hourly trước
    hourly: List[Dict[str, Any]] = []
    for item in (fc.get("list") or [])[:24]:  # ~24 bản ghi ~ 3h x 24 = 72h, nhưng giới hạn 24 mục đầu
        w = (item.get("weather") or [{}])[0]
        hourly.append({
            "dt_local": _to_local_ts(int(item.get("dt", 0)), tz_offset),
            "temp": (item.get("main") or {}).get("temp"),
            "pop": item.get("pop", 0),
            "humidity": (item.get("main") or {}).get("humidity"),
            "wind_speed": (item.get("wind") or {}).get("speed"),
            "desc": w.get("description"),
            "icon": w.get("icon"),
        })

    # Gom ngày: min/max theo ngày địa phương
    by_date: Dict[str, Dict[str, Any]] = {}
    for it in (fc.get("list") or []):
        ts = _to_local_ts(int(it.get("dt", 0)), tz_offset)
        day = time.strftime("%Y-%m-%d", time.gmtime(ts))
        temp = (it.get("main") or {}).get("temp")
        pop = it.get("pop", 0)
        w = (it.get("weather") or [{}])[0]
        if day not in by_date:
            by_date[day] = {
                "dt_local": ts,
                "t_min": temp, "t_max": temp,
                "pop": pop, "desc": w.get("description"), "icon": w.get("icon"),
            }
        else:
            by_date[day]["t_min"] = min(by_date[day]["t_min"], temp)
            by_date[day]["t_max"] = max(by_date[day]["t_max"], temp)
            by_date[day]["pop"] = max(by_date[day]["pop"], pop)

    daily = list(by_date.values())[:7]

    return {
        "source": "current+forecast_2_5",
        "tz_offset": tz_offset,
        "current": current,
        "hourly": hourly,
        "daily": daily,
    }

def get_weather(lat: float, lon: float, units: str = OPENWEATHER_UNITS, lang: str = OPENWEATHER_LANG) -> Dict[str, Any]:
    """
    Trả về dict gồm: source, tz_offset, current, hourly (<=24), daily (<=8)
    """
    _ensure_api_key()
    # Ưu tiên One Call 3.0
    if OPENWEATHER_USE_ONECALL:
        try:
            oc = _fetch_onecall(lat, lon, units, lang)
            if "current" in oc:
                return _normalize_onecall(oc)
        except Exception:
            pass

    # Fallback sang 2.5 (weather + forecast)
    cur = _fetch_current(lat, lon, units, lang)
    fc = _fetch_forecast(lat, lon, units, lang)
    return _normalize_from_current_forecast(cur, fc)

def deg_to_text(deg: float) -> str:
    # Chuyển 0-360 thành ký hiệu hướng gió
    dirs = ["B", "B-Đ", "Đ", "N-Đ", "N", "N-T", "T", "B-T"]
    ix = int((deg + 22.5) // 45) % 8 if isinstance(deg, (int, float)) else 0
    return dirs[ix]

def add_openweather_tile_layers(m):
    """
    Thêm các lớp tile overlay từ OpenWeather vào folium Map.
    Lưu ý: yêu cầu appid (API key). Có thể bật/tắt trong config.
    """
    if not ENABLE_WEATHER_TILES:
        return

    if not OPENWEATHER_API_KEY:
        return

    # Các lớp phổ biến: clouds_new, precipitation_new, pressure_new, wind_new, temp_new
    tile_layers = {
        "🌥 Mây (clouds)": "https://tile.openweathermap.org/map/clouds_new/{z}/{x}/{y}.png?appid=",
        "🌧 Mưa (precip.)": "https://tile.openweathermap.org/map/precipitation_new/{z}/{x}/{y}.png?appid=",
        "🌡 Nhiệt độ": "https://tile.openweathermap.org/map/temp_new/{z}/{x}/{y}.png?appid=",
        "🧭 Gió": "https://tile.openweathermap.org/map/wind_new/{z}/{x}/{y}.png?appid=",
        "⚖️ Áp suất": "https://tile.openweathermap.org/map/pressure_new/{z}/{x}/{y}.png?appid=",
    }
    import folium
    for name, base in tile_layers.items():
        folium.TileLayer(
            tiles=f"{base}{OPENWEATHER_API_KEY}",
            name=name,
            attr="OpenWeatherMap",
            overlay=True,
            control=True,
            opacity=WEATHER_TILE_OPACITY,
        ).add_to(m)
