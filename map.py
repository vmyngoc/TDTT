import os, re, time, math, requests, pandas as pd
import streamlit as st
import folium
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

# ====== THỜI TIẾT ======
from weather import get_weather, add_openweather_tile_layers, deg_to_text
from config import OPENWEATHER_LANG

# ===== CẤU HÌNH =====
OVERPASS_URLS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]
USER_AGENT = {"User-Agent": "VN-POI-Streamlit-Plus/1.2 (contact: example@gmail.com)"}
geolocator = Nominatim(user_agent="viet_poi_app")

st.set_page_config(page_title="Bản đồ POI Việt Nam", layout="wide")

st.markdown("""
    <h1 style='text-align:center; color:#2E86C1;'>🔍 Bản đồ tìm kiếm địa điểm & thời tiết Việt Nam</h1>
    <p style='text-align:center; color:gray;'>Tìm kiếm quán cà phê, nhà hàng, ngân hàng, siêu thị... quanh khu vực bạn chọn, kèm dự báo thời tiết cập nhật 🌤</p>
    <hr>
""", unsafe_allow_html=True)


# ===== TRẠNG THÁI =====
defaults = {"center_lat": 16.0, "center_lon": 108.0, "zoom": 5, "pois": []}
for k, v in defaults.items():
    st.session_state.setdefault(k, v)
st.session_state.setdefault("last_place", None)
st.session_state.setdefault("weather", None)

# ===== INPUT =====
place_name = st.text_input("📍 Nhập địa điểm (ví dụ: Hà Nội, Đà Nẵng):")

CATEGORIES = {
    "Quán cà phê (amenity=cafe)": ("amenity", "cafe"),
    "Nhà hàng (amenity=restaurant)": ("amenity", "restaurant"),
    "ATM (amenity=atm)": ("amenity", "atm"),
    "Ngân hàng (amenity=bank)": ("amenity", "bank"),
    "Siêu thị (shop=supermarket)": ("shop", "supermarket"),
    "Cửa hàng tiện lợi (shop=convenience)": ("shop", "convenience"),
    "Hiệu thuốc (amenity=pharmacy)": ("amenity", "pharmacy"),
    "Bệnh viện (amenity=hospital)": ("amenity", "hospital"),
    "Khách sạn (tourism=hotel)": ("tourism", "hotel"),
    "Nhà nghỉ (tourism=guest_house)": ("tourism", "guest_house"),
    "Trường học (amenity=school)": ("amenity", "school"),
    "Thư viện (amenity=library)": ("amenity", "library"),
    "Công viên (leisure=park)": ("leisure", "park"),
    "Trạm xăng (amenity=fuel)": ("amenity", "fuel"),
    "Bưu điện (amenity=post_office)": ("amenity", "post_office"),
}
selected_categories = st.multiselect(
    "🗂 Chọn loại địa điểm",
    list(CATEGORIES.keys()),
    default=["Quán cà phê (amenity=cafe)"]
)
keyword = st.text_input("🔎 Từ khóa tùy chọn (lọc theo tên/thương hiệu):", "")
radius_m = st.slider("📏 Bán kính tìm kiếm (m)", 200, 5000, 1000, step=100)
limit_n = st.slider("Số lượng POI hiển thị", 5, 100, 20, step=5)
search_button = st.button("🚀 Tìm kiếm POI & thời tiết")

# ===== HÀM PHỤ =====
@st.cache_data(ttl=600)
def geocode_safe(place):
    try:
        return geolocator.geocode(place + ", Vietnam", timeout=10)
    except (GeocoderTimedOut, GeocoderServiceError):
        time.sleep(1)
        return geocode_safe(place)

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = map(math.radians, [lat1, lat2])
    dphi, dlambda = map(math.radians, [lat2 - lat1, lon2 - lon1])
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def build_union_query(lat, lon, radius, kv_list, keyword=""):
    keyword = keyword.strip()
    regex_part = f'[~"name|brand"~"{re.escape(keyword)}",i]' if keyword else ""
    query = "[out:json][timeout:60];("
    for k, v in kv_list:
        query += f'nwr(around:{radius},{lat},{lon})["{k}"="{v}"]{regex_part};'
    query += ");out center tags;"
    return query

def overpass_request(query):
    for url in OVERPASS_URLS:
        try:
            r = requests.post(url, data=query.encode("utf-8"), headers=USER_AGENT, timeout=90)
            r.raise_for_status()
            return r.json()
        except Exception:
            time.sleep(0.8)
    raise RuntimeError("Không thể kết nối tới Overpass API. Vui lòng thử lại sau.")

def make_address(tags):
    parts = [tags.get(f"addr:{k}") for k in ["housenumber","street","city","province"] if tags.get(f"addr:{k}")]
    return ", ".join(parts) if parts else ""

@st.cache_data(ttl=600)
def fetch_pois(lat, lon, radius, kv_list, keyword, limit):
    data = overpass_request(build_union_query(lat, lon, radius, kv_list, keyword))
    elements = data.get("elements", [])
    seen = set()
    results = []
    for e in elements:
        eid, etype = e.get("id"), e.get("type", "node")
        if not eid or (etype, eid) in seen:
            continue
        seen.add((etype, eid))
        tags = e.get("tags", {})
        name = tags.get("name") or tags.get("brand") or "(không tên)"
        el_lat = e.get("lat") or (e.get("center") or {}).get("lat")
        el_lon = e.get("lon") or (e.get("center") or {}).get("lon")
        if not el_lat or not el_lon:
            continue
        dist = haversine(lat, lon, el_lat, el_lon)
        results.append({
            "id": eid,
            "osm_type": etype,
            "name": name,
            "lat": el_lat,
            "lon": el_lon,
            "distance_m": dist,
            "category": tags.get("amenity") or tags.get("shop") or tags.get("tourism") or tags.get("leisure"),
            "address": make_address(tags),
        })
    results.sort(key=lambda x: x["distance_m"])
    return results[:limit]

# ===== XỬ LÝ =====
def run_search(lat, lon, place_label):
    kv_list = [CATEGORIES[k] for k in selected_categories if k in CATEGORIES]
    try:
        pois = fetch_pois(lat, lon, radius_m, kv_list, keyword, limit_n)
        st.session_state.update({
            "pois": pois,
            "center_lat": lat,
            "center_lon": lon,
            "zoom": 14,
            "last_place": place_label,
        })
    except Exception as e:
        st.error(f"Lỗi truy vấn POI: {e}")

    try:
        st.session_state["weather"] = get_weather(lat, lon)
    except Exception as e:
        st.warning(f"Không lấy được dữ liệu thời tiết: {e}")

# ===== HIỂN THỊ KẾT QUẢ =====
if search_button and place_name.strip():
    loc = geocode_safe(place_name)
    if loc:
        run_search(loc.latitude, loc.longitude, place_name.strip())
    else:
        st.error("Không tìm thấy địa điểm này ở Việt Nam.")

if st.session_state.get("last_place"):
    st.markdown(
        f"**Tâm tìm kiếm:** {st.session_state.center_lat:.6f}, {st.session_state.center_lon:.6f} | "
        f"Bán kính: {radius_m} m | {len(st.session_state.pois)} kết quả"
    )

if st.session_state.get("weather") and st.session_state.get("last_place"):
    st.markdown(f"### 🌤 Thời tiết tại **{st.session_state['last_place']}**")
    w = st.session_state["weather"]
    if w and w.get("current"):
        cur = w["current"]
        st.write(f"Nhiệt độ: {cur.get('temp')}°C — {cur.get('desc') or '—'}")

if st.session_state.get("pois"):
    with st.expander("📍 Danh sách địa điểm tìm được", expanded=True):
        df = pd.DataFrame(st.session_state.pois)
        st.dataframe(df[["name","category","distance_m","address"]], use_container_width=True)
        csv = df.to_csv(index=False).encode("utf-8-sig")
        st.download_button("📥 Tải về CSV", csv, file_name="poi_results.csv", mime="text/csv")


# ===== BẢN ĐỒ =====
m = folium.Map(location=[st.session_state.center_lat, st.session_state.center_lon], zoom_start=st.session_state.zoom)
folium.Circle(
    location=[st.session_state.center_lat, st.session_state.center_lon],
    radius=radius_m, color="#3388ff", fill=True, fill_opacity=0.1
).add_to(m)
folium.Marker(
    [st.session_state.center_lat, st.session_state.center_lon],
    icon=folium.Icon(color="red", icon="star"), popup="Tâm tìm kiếm"
).add_to(m)

add_openweather_tile_layers(m)

if st.session_state.pois:
    cluster = MarkerCluster(name="POIs").add_to(m)
    for poi in st.session_state.pois:
        popup_html = f"""
        <b>{poi['name']}</b><br>
        Loại: {poi['category']}<br>
        Khoảng cách: {poi['distance_m']:.0f} m<br>
        Địa chỉ: {poi['address'] or '—'}<br>
        <a href="https://www.openstreetmap.org/{poi['osm_type']}/{poi['id']}" target="_blank">OSM</a> |
        <a href="https://www.google.com/maps/dir/?api=1&destination={poi['lat']},{poi['lon']}" target="_blank">Google Maps</a>
        """
        folium.Marker(
            [poi["lat"], poi["lon"]],
            popup=folium.Popup(popup_html, max_width=300),
            icon=folium.Icon(color="blue", icon="info-sign")
        ).add_to(cluster)
    folium.LayerControl(collapsed=False).add_to(m)

map_state = st_folium(m, width=800, height=520)
if map_state and map_state.get("last_clicked"):
    clicked = map_state["last_clicked"]
    lat, lon = clicked["lat"], clicked.get("lng") or clicked.get("lon")
    if st.button("📍 Dùng điểm vừa click làm tâm & tìm lại"):
        run_search(lat, lon, f"Điểm ({lat:.5f},{lon:.5f})")

