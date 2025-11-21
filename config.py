import os
from dotenv import load_dotenv

# Nạp biến môi trường từ .env nếu có
load_dotenv()

# ===== OpenWeather =====
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "").strip()
# Thử One Call 3.0 trước, nếu lỗi/không có quyền sẽ tự fallback sang API 2.5 (current + forecast)
OPENWEATHER_USE_ONECALL = os.getenv("OPENWEATHER_USE_ONECALL", "true").lower() == "true"
OPENWEATHER_UNITS = os.getenv("OPENWEATHER_UNITS", "metric")  # metric | imperial | standard
OPENWEATHER_LANG = os.getenv("OPENWEATHER_LANG", "vi")        # ngôn ngữ trả về mô tả thời tiết

# TTL cache (giây) để hạn chế số lần gọi API
CACHE_TTL = int(os.getenv("CACHE_TTL", "600"))

# Bật/ẩn các lớp tile overlay của OpenWeather trên bản đồ
ENABLE_WEATHER_TILES = os.getenv("ENABLE_WEATHER_TILES", "true").lower() == "true"
WEATHER_TILE_OPACITY = float(os.getenv("WEATHER_TILE_OPACITY", "0.55"))

print("Đã đọc API key:", "✅ Có key" if OPENWEATHER_API_KEY else "❌ Không có key")
