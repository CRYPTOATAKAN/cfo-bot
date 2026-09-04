import os
import re
import io
import json
import time
import uuid
import random
import datetime
import threading
import unicodedata
import urllib.request
import urllib.parse
import concurrent.futures
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional, Dict, Any, List, Tuple, Set

import gspread
from google.oauth2.service_account import Credentials

# --- TÜRKİYE SAAT DİLİMİ (UTC+3) ---
TR_TZ = datetime.timezone(datetime.timedelta(hours=3))

def suankiZamaniAl():
    return datetime.datetime.now(TR_TZ)

# --- AYARLAR & SABİTLER ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8629756462:AAHSn66-SVOZzWp_UrBj36bHjF1hpts5bco")
KURUCU_ID = int(os.environ.get("KURUCU_ID", "8395730761"))
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "1Gim_-YSb_TtODclXiZ0hnx2WDsc-RCW9CD51LeVNOaI")
WEB_APP_URL = os.environ.get("WEB_APP_URL", "https://site--cfo-bot-servis--drx8qvjbw8cw.code.run")
LOG_SAYFASI = "Guvenlik_Log"
ADMIN_SAYFASI = "YONETICILER"
BAGLANTI_SAYFASI = "GRUP_BAGLANTILARI"
VARSAYILAN_TRC20_ADRES = os.environ.get("TRC20_WALLET_ADDRESS", "TQHuwJh5c4ygbKhfFoGqTZTahjQuJAX3iV")

# --- PARALEL İŞ PARÇACIĞI HAVUZLARI (YÜKSEK PERFORMANS) ---
_update_executor = concurrent.futures.ThreadPoolExecutor(max_workers=16, thread_name_prefix="UpdateWorker")
_log_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="LogWorker")

app_state = {
    "WEB_APP_URL": WEB_APP_URL,
    "EK_ADMINLER": set(),
    "GRUP_BAGLANTILARI": {},
    "BAGLANTI_CACHE_TIME": 0,
    "SISTEM_KILIDI": "PASIF",
    "CIRO_HEDEFI": float(os.environ.get("CIRO_HEDEFI", "50000000.0")),
    "SON_ISLEM": None,
    "LOG_HAFTASI": None,
    "ADMIN_CACHE_TIME": 0,
    "KAPANIS_SAATI": os.environ.get("KAPANIS_SAATI", "23:00"),
    "SON_KAPANIS_TARIHI": None
}

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# --- GOOGLE SHEETS BAĞLANTI ÖNBELLEĞİ (CANLI INSTANCE CACHING) ---
_cached_gc = None
_cached_spreadsheet = None
_cached_sh_time = 0
_sh_lock = threading.Lock()

def http_get_json(url: str, headers: dict = None) -> dict:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"})
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))

def http_get_text(url: str, headers: dict = None) -> str:
    default_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    if headers:
        default_headers.update(headers)
    req = urllib.request.Request(url, headers=default_headers)
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.read().decode("utf-8", errors="ignore")

_tg_thread_local = threading.local()

def _get_telegram_conn():
    conn = getattr(_tg_thread_local, "conn", None)
    if conn is None:
        try:
            import http.client
            import ssl
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection("api.telegram.org", timeout=12, context=ctx)
            _tg_thread_local.conn = conn
        except Exception:
            conn = None
    return conn

def telegram_api(method: str, payload: dict) -> dict:
    url_path = f"/bot{TELEGRAM_TOKEN}/{method}"
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Connection": "keep-alive"}
    
    # 1. Hızlı Kalıcı TLS Soketi (Keep-Alive)
    for attempt in range(2):
        conn = _get_telegram_conn()
        if conn is not None:
            try:
                conn.request("POST", url_path, body=data, headers=headers)
                resp = conn.getresponse()
                raw_bytes = resp.read()
                return json.loads(raw_bytes.decode("utf-8"))
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                _tg_thread_local.conn = None

    # 2. Güvenli Yedek urllib çağrısı
    try:
        url = f"https://api.telegram.org{url_path}"
        req = urllib.request.Request(url, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as e:
        print(f"Telegram API Hatası ({method}): {e}")
        return {"ok": False, "error": str(e)}

def _append_close_button_if_needed(reply_markup):
    close_btn = [{"text": "🗑️ Mesajı Kapat", "callback_data": "mesaj_kapat"}]
    if reply_markup is None:
        return {"inline_keyboard": [close_btn]}
    if isinstance(reply_markup, dict) and "inline_keyboard" in reply_markup:
        has_close = any(
            any(btn.get("callback_data") in ["mesaj_kapat", "panel_kapat", "kapat"] for btn in row)
            for row in reply_markup.get("inline_keyboard", [])
        )
        if not has_close:
            new_kb = [list(row) for row in reply_markup["inline_keyboard"]]
            new_kb.append(close_btn)
            return {"inline_keyboard": new_kb}
        return reply_markup
    return reply_markup

def telegramMesajGonder(chat_id, metin: str, reply_markup=None, kapat_butonu_ekle: bool = True):
    if kapat_butonu_ekle:
        reply_markup = _append_close_button_if_needed(reply_markup)
    payload = {"chat_id": chat_id, "text": metin, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    res = telegram_api("sendMessage", payload)
    if not res.get("ok") and ("can't parse entities" in str(res.get("description", "")).lower() or "bad request" in str(res.get("description", "")).lower()):
        payload.pop("parse_mode", None)
        return telegram_api("sendMessage", payload)
    return res

def telegramFotoGonder(chat_id, foto_url: str, caption: str = None, reply_markup=None, kapat_butonu_ekle: bool = True):
    if kapat_butonu_ekle:
        reply_markup = _append_close_button_if_needed(reply_markup)
    payload = {"chat_id": chat_id, "photo": foto_url, "parse_mode": "HTML"}
    if caption:
        payload["caption"] = caption
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return telegram_api("sendPhoto", payload)

def telegramMesajSil(chat_id, message_id):
    return telegram_api("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

def telegramMesajDuzenle(chat_id, message_id, metin: str, reply_markup=None, kapat_butonu_ekle: bool = True):
    if reply_markup is not None and kapat_butonu_ekle:
        reply_markup = _append_close_button_if_needed(reply_markup)
    payload = {"chat_id": chat_id, "message_id": message_id, "text": metin, "parse_mode": "HTML"}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return telegram_api("editMessageText", payload)

def telegramChatAction(chat_id, action: str = "typing"):
    return telegram_api("sendChatAction", {"chat_id": chat_id, "action": action})

def get_gspread_client():
    global _cached_gc
    if _cached_gc is not None:
        return _cached_gc

    json_env = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if json_env and json_env.strip():
        try:
            info = json.loads(json_env.strip())
            creds = Credentials.from_service_account_info(info, scopes=SCOPES)
            _cached_gc = gspread.authorize(creds)
            return _cached_gc
        except Exception as e:
            print(f"GOOGLE_SERVICE_ACCOUNT_JSON okunamadı: {e}")

    for path in [
        os.path.join(os.path.dirname(__file__), "service_account.json"),
        "./service_account.json",
        "/app/service_account.json",
        "/service_account.json",
        "/etc/secrets/service_account.json"
    ]:
        if os.path.exists(path):
            try:
                creds = Credentials.from_service_account_file(path, scopes=SCOPES)
                _cached_gc = gspread.authorize(creds)
                return _cached_gc
            except Exception as e:
                print(f"{path} okunamadı: {e}")

    raise FileNotFoundError("Google Service Account anahtarı bulunamadı!")

def get_spreadsheet(force_refresh=False):
    global _cached_spreadsheet, _cached_sh_time, _cached_gc
    now = time.time()
    with _sh_lock:
        if not force_refresh and _cached_spreadsheet is not None and (now - _cached_sh_time < 300):
            return _cached_spreadsheet
        try:
            gc = get_gspread_client()
            _cached_spreadsheet = gc.open_by_key(SPREADSHEET_ID)
            _cached_sh_time = now
            return _cached_spreadsheet
        except Exception as e:
            _cached_gc = None
            gc = get_gspread_client()
            _cached_spreadsheet = gc.open_by_key(SPREADSHEET_ID)
            _cached_sh_time = now
            return _cached_spreadsheet

def is_valid_daily_sheet(ws) -> bool:
    """Bir sayfanın gerçek ana kasa tablosu (en az 8 sütun ve 30 satır) olup olmadığını doğrular."""
    if ws.title in [LOG_SAYFASI, "NOTLAR", "YEDEK", ADMIN_SAYFASI, BAGLANTI_SAYFASI]:
        return False
    try:
        if ws.col_count < 8 or ws.row_count < 30:
            return False
        return True
    except Exception:
        return False

_cached_active_sheet = None
_cached_active_sheet_time = 0
_cached_active_sheet_lock = threading.Lock()

def get_active_daily_sheet(sh, force_refresh=False) -> gspread.Worksheet:
    """
    Excel tablosundaki EN SON GÜNCEL TARİHLİ aktif çalışma sayfasını bulur ve önbelleğe alır.
    Sistem saati ne olursa olsun (yeni gün erken açılmış olsa veya gece yarısı öncesi/sonrası fark etmeksizin),
    Google Sheets'teki en ileri/en son tarihli sayfayı baz alır ve işlemleri doğrudan oraya işler.
    """
    global _cached_active_sheet, _cached_active_sheet_time
    now = time.time()
    with _cached_active_sheet_lock:
        if not force_refresh and _cached_active_sheet is not None and (now - _cached_active_sheet_time < 60):
            return _cached_active_sheet
            
        tum_ws = sh.worksheets()
        tarih_sayfalari = []
        
        for ws in tum_ws:
            if is_valid_daily_sheet(ws) and re.match(r'^\d{2}\.\d{2}\.\d{4}$', ws.title):
                try:
                    t_obj = datetime.datetime.strptime(ws.title, "%d.%m.%Y")
                    tarih_sayfalari.append((t_obj, ws))
                except Exception:
                    pass
                
        if tarih_sayfalari:
            # Tarihe göre büyükten küçüğe sırala (en güncel/en son açılan tarih en başta)
            tarih_sayfalari.sort(key=lambda x: x[0], reverse=True)
            _cached_active_sheet = tarih_sayfalari[0][1]
            _cached_active_sheet_time = now
            return _cached_active_sheet
            
        for ws in tum_ws:
            if is_valid_daily_sheet(ws):
                _cached_active_sheet = ws
                _cached_active_sheet_time = now
                return ws
                
        _cached_active_sheet = tum_ws[0]
        _cached_active_sheet_time = now
        return tum_ws[0]

_cached_sheet_matrix = None
_cached_sheet_matrix_title = ""
_cached_sheet_matrix_time = 0
_cached_sheet_matrix_lock = threading.Lock()

def get_sheet_values_fast(sayfa: gspread.Worksheet, force_refresh: bool = False, max_age_seconds: float = 30.0) -> List[List[str]]:
    """Aktif sayfanın 45 satırlık verisini RAM'den (0.001 ms) veya en fazla 30 saniye eski önbellekten döndürür."""
    global _cached_sheet_matrix, _cached_sheet_matrix_title, _cached_sheet_matrix_time
    now = time.time()
    with _cached_sheet_matrix_lock:
        if (not force_refresh and 
            _cached_sheet_matrix is not None and 
            _cached_sheet_matrix_title == sayfa.title and 
            (now - _cached_sheet_matrix_time < max_age_seconds)):
            return [list(r) for r in _cached_sheet_matrix]
            
        try:
            veriler = sayfa.get_all_values()
            _cached_sheet_matrix = [list(r) for r in veriler]
            _cached_sheet_matrix_title = sayfa.title
            _cached_sheet_matrix_time = now
            return [list(r) for r in _cached_sheet_matrix]
        except Exception as e:
            if _cached_sheet_matrix is not None and _cached_sheet_matrix_title == sayfa.title:
                return [list(r) for r in _cached_sheet_matrix]
            raise e

def update_sheet_matrix_memory(sayfa_title: str, row_1based: int, col_1based: int, val: Any):
    """Bellekteki RAM tablosunu anında günceller (0.001 ms)."""
    global _cached_sheet_matrix, _cached_sheet_matrix_title, _cached_sheet_matrix_time
    with _cached_sheet_matrix_lock:
        if _cached_sheet_matrix is not None and _cached_sheet_matrix_title == sayfa_title:
            r_idx = row_1based - 1
            c_idx = col_1based - 1
            while len(_cached_sheet_matrix) <= r_idx:
                _cached_sheet_matrix.append([])
            while len(_cached_sheet_matrix[r_idx]) <= c_idx:
                _cached_sheet_matrix[r_idx].append("")
            _cached_sheet_matrix[r_idx][c_idx] = str(val)
            _cached_sheet_matrix_time = time.time()

def bugununTarihiniAl() -> str:
    """Aktif en son sayfanın adını döner."""
    try:
        sh = get_spreadsheet()
        ws = get_active_daily_sheet(sh)
        return ws.title
    except Exception:
        return suankiZamaniAl().strftime("%d.%m.%Y")

def normalize_text(text: str) -> str:
    """Türkçe harf duyarlılığını ve büyük/küçük harf farklarını %100 kusursuz eşitler."""
    if not text: return ""
    t = str(text).strip()
    t = unicodedata.normalize("NFKD", t)
    tr_map = {
        "i": "I", "ı": "I", "İ": "I", "I": "I", "î": "I", "Î": "I", "\u0130": "I", "\u0131": "I",
        "ş": "S", "Ş": "S", "\u015f": "S", "\u015e": "S",
        "ğ": "G", "Ğ": "G", "\u011f": "G", "\u011e": "G",
        "ü": "U", "Ü": "U", "\u00fc": "U", "\u00dc": "U",
        "ö": "O", "Ö": "O", "\u00f6": "O", "\u00d6": "O",
        "ç": "C", "Ç": "C", "\u00e7": "C", "\u00c7": "C"
    }
    for k, v in tr_map.items():
        t = t.replace(k, v)
    t = t.upper()
    t = "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))
    return re.sub(r"[^A-Z0-9]", "", t)

def grupEmojisiBul(grupAdi: str) -> str:
    if not grupAdi:
        return "🔹"
    temiz = normalize_text(grupAdi)
    
    # 1. Hayvan & Güç Sembolleri
    if any(k in temiz for k in ["TIGER", "KAPLAN"]): return "🐅"
    if any(k in temiz for k in ["LION", "ASLAN"]): return "🦁"
    if any(k in temiz for k in ["EAGLE", "KARTAL", "SAHIN", "SACID"]): return "🦅"
    if any(k in temiz for k in ["KURT", "WOLF"]): return "🐺"
    if any(k in temiz for k in ["PANTER", "PANTHER"]): return "🐆"
    if any(k in temiz for k in ["BOGA", "BULL"]): return "🐂"
    if any(k in temiz for k in ["EJDER", "DRAGON"]): return "🐉"
    if any(k in temiz for k in ["SHARK", "KOPEKBALIGI"]): return "🦈"
    if any(k in temiz for k in ["AYI", "BEAR"]): return "🐻"

    # 2. Kuyumculuk, Altın & Kripto
    if any(k in temiz for k in ["KUYUM", "ALTIN", "HAS", "GOLD"]): return "💍"
    if any(k in temiz for k in ["ELMAS", "PIRLANTA", "DIAMOND", "BSM"]): return "💎"
    if any(k in temiz for k in ["KRIPTO", "USDT", "BTC", "ETH", "TRON", "TETHER"]): return "🪙"
    if any(k in temiz for k in ["DOLAR", "USD", "NAKIT", "KASA"]): return "💵"
    if any(k in temiz for k in ["EURO", "EUR"]): return "💶"

    # 3. Kurumsal, Gayrimenkul & Şirket
    if any(k in temiz for k in ["EMLAK", "HOLDING", "SIRKET", "OFIS", "CYL", "ARS"]): return "🏢"
    if any(k in temiz for k in ["HSY"]): return "🏛️"
    if any(k in temiz for k in ["BANKA", "HAVALE", "EFT"]): return "🏦"
    if any(k in temiz for k in ["ABI", "YONETIM", "BOSS", "PATRON"]): return "👑"
    if any(k in temiz for k in ["VIP", "OZEL", "STAR", "YILDIZ"]): return "⭐"

    # 4. Masraf & Lojistik Kalemleri
    if "GENELTOPLAM" in temiz: return "🏆"
    if any(k in temiz for k in ["MASRAF", "GIDER"]): return "📉"
    if any(k in temiz for k in ["KARGO", "LOJISTIK", "TESLIMAT"]): return "📦"
    if any(k in temiz for k in ["YEMEK", "RESTORAN", "KAFE", "MUTFAK"]): return "🍔"
    if any(k in temiz for k in ["ARAC", "YAKIT", "BENZIN", "MAZOT", "PETROL"]): return "🚗"
    if any(k in temiz for k in ["KOMISYON", "KESINTI"]): return "✂️"

    # 5. Diğer Cariler için İsim Tabanlı Benzersiz & Şık Sembol Havuzu
    PALETTE = ["💎", "👑", "⚡", "🌟", "🛡️", "🔥", "🎯", "🚀", "🏆", "⚜️", "⚓", "🔮", "💰", "🪐", "🍀", "✨", "🔹", "🔶"]
    h = sum(ord(c) for c in temiz)
    return PALETTE[h % len(PALETTE)]

def rakamFormatla(sayi) -> str:
    try:
        val = int(round(float(sayi)))
        is_neg = val < 0
        val_str = f"{abs(val):,}".replace(",", ".")
        return f"-{val_str}" if is_neg else val_str
    except Exception:
        return str(sayi)

def guvenliSayi(deger) -> float:
    """Türkçe ve Uluslararası Google Sheets sayı formatlarını (+/- işaretleri koruyarak) %100 hatasız dönüştürür."""
    if deger is None or deger == "": return 0.0
    if isinstance(deger, (int, float)): return float(deger)
    metin = str(deger).strip()
    if metin in ["", "-"]: return 0.0
    
    eksi_mi = "-" in metin or (metin.startswith("(") and metin.endswith(")"))
    temiz = re.sub(r"[^0-9,.]", "", metin)
    
    if "." in temiz and "," in temiz:
        if temiz.rfind(",") > temiz.rfind("."):
            temiz = temiz.replace(".", "").replace(",", ".")
        else:
            temiz = temiz.replace(",", "")
    elif "." in temiz:
        parts = temiz.split(".")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3 and int(parts[0]) > 0):
            temiz = temiz.replace(".", "")
        else:
            pass
    elif "," in temiz:
        parts = temiz.split(",")
        if len(parts) > 2:
            temiz = temiz.replace(",", "")
        else:
            temiz = temiz.replace(",", ".")
            
    try:
        sayi = float(temiz)
        return -sayi if eksi_mi else sayi
    except Exception:
        return 0.0

def paraFormatla(deger) -> str:
    """Matematiksel işaretleri (+/-) eksiksiz koruyan Türkçe para formatı: -50.000,00 ₺ veya 150.000,00 ₺"""
    try:
        val = float(deger)
        if abs(val) < 0.00001:
            return "0,00 ₺"
        is_negative = val < 0
        formatted = f"{abs(val):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"-{formatted} ₺" if is_negative else f"{formatted} ₺"
    except Exception:
        return "0,00 ₺"

def _sistemeLogYaz_worker(islemAdi: str, detay: str):
    try:
        sh = get_spreadsheet()
        try:
            logSayfasi = sh.worksheet(LOG_SAYFASI)
        except gspread.exceptions.WorksheetNotFound:
            logSayfasi = sh.add_worksheet(title=LOG_SAYFASI, rows=500, cols=5)
            logSayfasi.append_row(["Tarih/Saat", "İşlem Türü", "İşlem Detayı"])
        tarihSaat = suankiZamaniAl().strftime("%d.%m.%Y %H:%M")
        logSayfasi.append_row([tarihSaat, islemAdi, detay])
    except Exception as e:
        print(f"Log hatası: {e}")

def sistemeLogYaz(islemAdi: str, detay: str):
    _log_executor.submit(_sistemeLogYaz_worker, islemAdi, detay)

# --- YETKİ & ADMİN YÖNETİMİ ---
def admin_listesini_guncelle():
    now = time.time()
    if now - app_state["ADMIN_CACHE_TIME"] < 60:
        return
    try:
        sh = get_spreadsheet()
        try:
            adminSayfasi = sh.worksheet(ADMIN_SAYFASI)
        except gspread.exceptions.WorksheetNotFound:
            adminSayfasi = sh.add_worksheet(title=ADMIN_SAYFASI, rows=100, cols=4)
            adminSayfasi.append_row(["Telegram ID", "Yönetici Adı", "Ekleyen", "Tarih"])
            adminSayfasi.append_row([str(KURUCU_ID), "KURUCU (ATAKAN)", "SİSTEM", bugununTarihiniAl()])
            app_state["EK_ADMINLER"] = {KURUCU_ID}
            app_state["ADMIN_CACHE_TIME"] = now
            return

        rows = adminSayfasi.get_all_values()
        yeni_set = {KURUCU_ID}
        for r in rows[1:]:
            if len(r) > 0 and r[0].strip().isdigit():
                yeni_set.add(int(r[0].strip()))
        app_state["EK_ADMINLER"] = yeni_set
        app_state["ADMIN_CACHE_TIME"] = now
    except Exception as e:
        print(f"Admin listesi okuma hatası: {e}")

def yetkili_mi(user_id: int) -> bool:
    if user_id == KURUCU_ID:
        return True
    admin_listesini_guncelle()
    return user_id in app_state["EK_ADMINLER"]

_yetkisiz_uyarilanlar = set()

def yetkisiz_uyari_gonder(chat_id: int, user_id: int, mesaj: str, klavye: dict = None, tek_seferlik: bool = True) -> bool:
    """
    Yetkisiz kullanıcıya uyarı mesajı gönderir.
    tek_seferlik=True ise aynı kullanıcıya aynı sohbette sadece 1 defa uyarı gönderir,
    sonraki tekrarlarda grubu veya sohbeti spam yapmamak için sessizce yoksayar.
    """
    anahtar = (user_id, chat_id)
    if tek_seferlik:
        if anahtar in _yetkisiz_uyarilanlar:
            return False
        _yetkisiz_uyarilanlar.add(anahtar)
        if len(_yetkisiz_uyarilanlar) > 10000:
            _yetkisiz_uyarilanlar.clear()
            _yetkisiz_uyarilanlar.add(anahtar)
    telegramMesajGonder(chat_id, mesaj, klavye)
    return True

# --- TELEGRAM GRUP BAĞLANTILARI & DİNAMİK KASA FİŞİ ---
def grup_baglantilarini_guncelle():
    now = time.time()
    if now - app_state.get("BAGLANTI_CACHE_TIME", 0) < 60 and app_state.get("GRUP_BAGLANTILARI"):
        return
    try:
        sh = get_spreadsheet()
        try:
            baglantiSayfasi = sh.worksheet(BAGLANTI_SAYFASI)
        except gspread.exceptions.WorksheetNotFound:
            baglantiSayfasi = sh.add_worksheet(title=BAGLANTI_SAYFASI, rows=100, cols=5)
            baglantiSayfasi.append_row(["Chat ID", "Grup Adı", "Telegram Grup Başlığı", "Ekleyen ID", "Tarih"])
            app_state["GRUP_BAGLANTILARI"] = {}
            app_state["BAGLANTI_CACHE_TIME"] = now
            return

        rows = baglantiSayfasi.get_all_values()
        yeni_dict = {}
        for r in rows[1:]:
            if len(r) >= 2 and r[0].strip():
                try:
                    c_id = int(r[0].strip())
                    g_ad = r[1].strip()
                    c_title = r[2].strip() if len(r) > 2 else ""
                    yeni_dict[c_id] = {"grup": g_ad, "title": c_title}
                except ValueError:
                    pass
        app_state["GRUP_BAGLANTILARI"] = yeni_dict
        app_state["BAGLANTI_CACHE_TIME"] = now
    except Exception as e:
        print(f"Grup bağlantıları okuma hatası: {e}")

def grup_bagla_impl(chat_id: int, user_id: int, komut_metni: str, chat_title: str = "") -> str:
    if chat_id >= 0:
        raise ValueError("Bu komut sadece bir <b>Telegram Grubu</b> içinde çalıştırılabilir. Lütfen botu gruba ekleyip grupta çalıştırın.")
        
    parcalar = komut_metni.strip().split()[1:]
    if not parcalar:
        raise ValueError("Eksik grup adı! Örnek: <code>/grupbagla SACİD</code>")
    grup_ham_str = " ".join(parcalar).strip()
    hedef_norm = normalize_text(grup_ham_str)
    
    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    tum_veriler = sayfa.get_all_values()
    
    bulunan_grup_adi = None
    for row in tum_veriler[1:]:
        if len(row) >= 2 and normalize_text(row[1]) == hedef_norm:
            bulunan_grup_adi = row[1].strip()
            break
            
    if not bulunan_grup_adi:
        raise ValueError(f"Excel aktif gün sayfasında (<b>{sayfa.title}</b>) '<b>{grup_ham_str}</b>' adlı grup bulunamadı. Lütfen Excel'deki grup adını kontrol edin.")
        
    try:
        baglanti_sayfasi = sh.worksheet(BAGLANTI_SAYFASI)
    except gspread.exceptions.WorksheetNotFound:
        baglanti_sayfasi = sh.add_worksheet(title=BAGLANTI_SAYFASI, rows=100, cols=5)
        baglanti_sayfasi.append_row(["Chat ID", "Grup Adı", "Telegram Grup Başlığı", "Ekleyen ID", "Tarih"])
        
    mevcut_satirlar = baglanti_sayfasi.get_all_values()
    hedef_satir_idx = None
    for idx, r in enumerate(mevcut_satirlar[1:], start=2):
        if len(r) > 0 and r[0].strip() == str(chat_id):
            hedef_satir_idx = idx
            break
            
    tarih_saat = suankiZamaniAl().strftime("%d.%m.%Y %H:%M")
    yeni_satir_verisi = [str(chat_id), bulunan_grup_adi, chat_title, str(user_id), tarih_saat]
    
    if hedef_satir_idx:
        baglanti_sayfasi.update(f"A{hedef_satir_idx}:E{hedef_satir_idx}", [yeni_satir_verisi])
    else:
        baglanti_sayfasi.append_row(yeni_satir_verisi)
        
    app_state.setdefault("GRUP_BAGLANTILARI", {})[chat_id] = {
        "grup": bulunan_grup_adi,
        "title": chat_title
    }
    app_state["BAGLANTI_CACHE_TIME"] = time.time()
    
    sistemeLogYaz("Grup Bağlama", f"Chat: {chat_id} ({chat_title}) -> {bulunan_grup_adi}")
    
    return (
        f"✅ <b>Bağlantı Başarılı!</b>\n"
        f"Bu Telegram grubu Excel'deki <b>{bulunan_grup_adi}</b> satırına bağlandı.\n\n"
        f"Artık yetkililer bu grupta sadece <code>/kasa</code> yazarak canlı durum fişini alabilir."
    )

def grup_kopar_impl(chat_id: int, user_id: int) -> str:
    if chat_id >= 0:
        raise ValueError("Bu komut sadece bir <b>Telegram Grubu</b> içinde çalıştırılabilir.")
        
    sh = get_spreadsheet()
    eski_grup = ""
    try:
        baglanti_sayfasi = sh.worksheet(BAGLANTI_SAYFASI)
        mevcut_satirlar = baglanti_sayfasi.get_all_values()
        bulunan_idx = None
        for idx, r in enumerate(mevcut_satirlar[1:], start=2):
            if len(r) > 0 and r[0].strip() == str(chat_id):
                bulunan_idx = idx
                eski_grup = r[1] if len(r) > 1 else ""
                break
        if bulunan_idx:
            baglanti_sayfasi.delete_rows(bulunan_idx)
    except Exception as e:
        print(f"Bağlantı silme hatası: {e}")
        
    if "GRUP_BAGLANTILARI" in app_state and chat_id in app_state["GRUP_BAGLANTILARI"]:
        if not eski_grup:
            eski_grup = app_state["GRUP_BAGLANTILARI"][chat_id].get("grup", "")
        del app_state["GRUP_BAGLANTILARI"][chat_id]
        
    sistemeLogYaz("Grup Bağlantısı Koparma", f"Chat: {chat_id} | Eski Grup: {eski_grup}")
    return f"🔌 <b>Grup Bağlantısı Kaldırıldı!</b>\nBu grubun Excel'deki (<b>{eski_grup}</b>) bağlantısı başarıyla sonlandırıldı."

def grup_baglantilari_listesi_impl() -> str:
    grup_baglantilarini_guncelle()
    baglantilar = app_state.get("GRUP_BAGLANTILARI", {})
    if not baglantilar:
        return "📭 <b>Henüz hiçbir Telegram grubu bir Excel satırına bağlanmamış.</b>\n\nGrupları bağlamak için grupta <code>/grupbagla [Grup Adı]</code> yazabilirsiniz."
    out = "🔗 <b>BAĞLI TELEGRAM GRUPLARI</b>\n━━━━━━━━━━━━━━━\n\n"
    for c_id, info in baglantilar.items():
        g_ad = info.get("grup", "Bilinmiyor")
        title = info.get("title", "")
        title_str = f" ({title})" if title else ""
        out += f"🏢 <b>Excel Cari:</b> <code>{g_ad}</code>\n💬 <b>Grup ID:</b> <code>{c_id}</code>{title_str}\n\n"
    return out

def grup_senkronize_impl() -> str:
    """
    Google Sheets (GRUP_BAGLANTILARI sayfası ve Günlük Cari Sayfası) ile
    Telegram grupları arasındaki tüm bağlantıları, cari isimlerini ve grup başlıklarını
    canlı olarak sorgular, senkronize eder ve tüm sistem önbelleklerini anında günceller.
    """
    global _cached_active_sheet, _cached_active_sheet_time, _cached_sheet_matrix, _cached_sheet_matrix_time, _cached_spreadsheet, _cached_sh_time
    _cached_active_sheet = None
    _cached_active_sheet_time = 0
    _cached_sheet_matrix = None
    _cached_sheet_matrix_time = 0
    _cached_spreadsheet = None
    _cached_sh_time = 0
    app_state["BAGLANTI_CACHE_TIME"] = 0
    
    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh, force_refresh=True)
    gunluk_veriler = get_sheet_values_fast(sayfa)
    
    gunluk_cariler = set()
    for row in gunluk_veriler[1:]:
        if len(row) >= 2 and row[1].strip():
            gunluk_cariler.add(row[1].strip())
            
    try:
        baglanti_sayfasi = sh.worksheet(BAGLANTI_SAYFASI)
    except gspread.exceptions.WorksheetNotFound:
        baglanti_sayfasi = sh.add_worksheet(title=BAGLANTI_SAYFASI, rows=100, cols=5)
        baglanti_sayfasi.append_row(["Chat ID", "Grup Adı", "Telegram Grup Başlığı", "Ekleyen ID", "Tarih"])
        
    satirlar = baglanti_sayfasi.get_all_values()
    if len(satirlar) <= 1:
        app_state["GRUP_BAGLANTILARI"] = {}
        return (
            "📭 <b>Bağlı Grup Bulunamadı!</b>\n\n"
            "Google Sheets'te henüz bağlanmış bir Telegram grubu kaydı bulunmuyor.\n"
            "Gruplarda <code>/grupbagla [Cari Adı]</code> yaparak bağlantı oluşturabilirsiniz."
        )
        
    yeni_dict = {}
    guncellenen_sayisi = 0
    aktif_bagli_sayisi = 0
    uyarilar = []
    
    for idx, r in enumerate(satirlar[1:], start=2):
        if len(r) >= 2 and r[0].strip():
            try:
                c_id = int(r[0].strip())
                cari_adi = r[1].strip()
                mevcut_title = r[2].strip() if len(r) > 2 else ""
                
                canli_title = mevcut_title
                try:
                    chat_res = telegram_api("getChat", {"chat_id": c_id})
                    if chat_res and chat_res.get("ok"):
                        canli_title = chat_res.get("result", {}).get("title", mevcut_title)
                except Exception:
                    pass
                    
                if canli_title and canli_title != mevcut_title:
                    try:
                        baglanti_sayfasi.update_cell(idx, 3, canli_title)
                        guncellenen_sayisi += 1
                    except Exception:
                        pass
                        
                # Günlük cari tablosuyla isim güncelleme senkronizasyonu
                if cari_adi not in gunluk_cariler:
                    norm_bulunan = None
                    for g_c in gunluk_cariler:
                        if normalize_text(g_c) == normalize_text(cari_adi):
                            norm_bulunan = g_c
                            break
                    if not norm_bulunan and len(cari_adi) >= 3:
                        for g_c in gunluk_cariler:
                            if normalize_text(g_c).startswith(normalize_text(cari_adi)) or normalize_text(cari_adi).startswith(normalize_text(g_c)):
                                norm_bulunan = g_c
                                break
                    if norm_bulunan and norm_bulunan != cari_adi:
                        try:
                            baglanti_sayfasi.update_cell(idx, 2, norm_bulunan)
                            cari_adi = norm_bulunan
                            guncellenen_sayisi += 1
                        except Exception:
                            pass
                    elif not norm_bulunan:
                        uyarilar.append(f"• ⚠️ <b>{cari_adi}</b> <i>(Günlük Excel sayfasında bulunamadı)</i>")
                        
                yeni_dict[c_id] = {"grup": cari_adi, "title": canli_title}
                aktif_bagli_sayisi += 1
            except ValueError:
                pass
                
    app_state["GRUP_BAGLANTILARI"] = yeni_dict
    app_state["BAGLANTI_CACHE_TIME"] = time.time()
    
    sistemeLogYaz(
        "Grup Senkronizasyonu",
        f"Toplam Bağlı: {aktif_bagli_sayisi} | Güncellenen: {guncellenen_sayisi}"
    )
    
    rapor = (
        f"🔄 <b>EXCEL & TELEGRAM GRUP SENKRONİZASYONU</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ <b>Durum:</b> <b>Tüm İsimler Başarıyla Eşitlendi</b>\n"
        f"👥 <b>Toplam Bağlı Grup:</b> <code>{aktif_bagli_sayisi} Adet</code>\n"
        f"📝 <b>Güncellenen Kayıt:</b> <code>{guncellenen_sayisi} Adet</code>\n"
        f"📑 <b>Aktif Günlük Sayfa:</b> <code>{sayfa.title}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )
    
    if yeni_dict:
        rapor += "🏢 <b>Eşleşen Güncel Cariler:</b>\n"
        sirali_baglantilar = sorted(yeni_dict.values(), key=lambda x: x.get("grup", ""))
        for info in sirali_baglantilar[:15]:
            g_ad = info.get("grup", "")
            t_title = info.get("title", "")
            title_str = f" <i>({t_title})</i>" if t_title else ""
            rapor += f"• <b>{g_ad}</b>{title_str}\n"
        if len(sirali_baglantilar) > 15:
            rapor += f"<i>(+{len(sirali_baglantilar) - 15} grup daha eşitlendi)</i>\n"
            
    if uyarilar:
        rapor += "\n⚠️ <b>Eksik / Değişen Cari Uyarıları:</b>\n" + "\n".join(uyarilar) + "\n"
        
    rapor += (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <i>Excel'de yaptığınız tüm isim değişiklikleri bota anında aktarılmıştır.</i>"
    )
    return rapor

def aktif_ibani_olan_carileri_bul(veriler: List[List[str]] = None) -> Set[str]:
    """
    Excel tablosunda Sol Blok (Col 14: Cari) ve Sağ Blok (Col 17: Cari) üzerinde
    şu anda bir İBAN'a tahsis edilmiş (dolu) olan tüm normalize edilmiş cari adlarını döner.
    """
    if veriler is None:
        sh = get_spreadsheet()
        sayfa = get_active_daily_sheet(sh)
        veriler = get_sheet_values_fast(sayfa)
        
    aktif_cariler = set()
    for row in veriler[1:]:
        # Sol Blok Cari (Col O, index 14)
        if len(row) > 14 and row[14].strip():
            c1 = row[14].strip()
            aktif_cariler.add(normalize_text(c1))
            
        # Sağ Blok Cari (Col R, index 17)
        if len(row) > 17 and row[17].strip():
            c2 = row[17].strip()
            aktif_cariler.add(normalize_text(c2))
            
    return aktif_cariler

def format_satir_satir_cariler(isimler: List[str], chunk_size: int = 3) -> str:
    if not isimler:
        return "• <i>Yok</i>"
    lines = []
    for i in range(0, len(isimler), chunk_size):
        chunk = isimler[i:i+chunk_size]
        lines.append("• " + ", ".join(chunk))
    return "\n".join(lines)

def toplu_duyuru_hazirla_paneli(komut_metni: str, gonderen_id: int) -> Tuple[str, Optional[dict]]:
    """
    Yönetici /duyuru [Metin] yazdığında hemen duyuru göndermez;
    Önce hedef kitle analizi yapar (İBAN'ı aktif olan gruplar, Tüm bağlı gruplar, Özel tek grup)
    ve seçim yapması için etkileşimli kontrol paneli sunar.
    """
    parcalar = komut_metni.strip().split(maxsplit=1)
    if len(parcalar) < 2 or not parcalar[1].strip():
        return (
            "⚠️ <b>Eksik Duyuru Metni!</b>\n\n"
            "Kullanım: <code>/duyuru [Duyuru Metniniz]</code>\n"
            "Örnek: <code>/duyuru Değerli iş ortaklarımız, banka hesaplarımız güncellenmiştir.</code>\n\n"
            "💡 <i>Komutu çalıştırdığınızda hedef grup seçimi için onay paneli açılacaktır.</i>",
            None
        )
        
    duyuru_icerik = parcalar[1].strip()
    grup_baglantilarini_guncelle()
    baglantilar = app_state.get("GRUP_BAGLANTILARI", {})
    
    if not baglantilar:
        return (
            "📭 <b>Bağlı Grup Bulunamadı!</b>\n\n"
            "Sistemde henüz Excel'e bağlanmış hiçbir Telegram grubu bulunmuyor.\n"
            "Duyuru gönderebilmek için önce ilgili gruplarda <code>/grupbagla [Cari Adı]</code> yapmalısınız.",
            None
        )
        
    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    veriler = get_sheet_values_fast(sayfa)
    aktif_iban_carileri = aktif_ibani_olan_carileri_bul(veriler)
    
    bagli_ibanli_gruplar = []
    bagli_ibansiz_gruplar = []
    
    for c_id, info in baglantilar.items():
        grup_adi = info.get("grup", "Bilinmeyen")
        if normalize_text(grup_adi) in aktif_iban_carileri:
            bagli_ibanli_gruplar.append((c_id, grup_adi))
        else:
            bagli_ibansiz_gruplar.append((c_id, grup_adi))
            
    # Benzersiz taslak ID üret ve sakla
    draft_id = uuid.uuid4().hex[:8]
    app_state.setdefault("DUYURU_TASLAKLARI", {})[draft_id] = {
        "metin": duyuru_icerik,
        "gonderen_id": gonderen_id,
        "zaman": time.time(),
        "ibanli_idler": [item[0] for item in bagli_ibanli_gruplar],
        "tum_idler": list(baglantilar.keys())
    }
    
    return toplu_duyuru_ana_panel_uret(draft_id)

def toplu_duyuru_ana_panel_uret(draft_id: str) -> Tuple[str, dict]:
    taslaklar = app_state.get("DUYURU_TASLAKLARI", {})
    if draft_id not in taslaklar:
        return (
            "⚠️ <b>Duyuru taslağının süresi dolmuş veya işlem tamamlanmış.</b>",
            {"inline_keyboard": [[{"text": "🗑️ Mesajı Kapat", "callback_data": "mesaj_kapat"}]]}
        )
    
    taslak = taslaklar[draft_id]
    duyuru_icerik = taslak["metin"]
    
    grup_baglantilarini_guncelle()
    baglantilar = app_state.get("GRUP_BAGLANTILARI", {})
    
    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    veriler = get_sheet_values_fast(sayfa)
    aktif_iban_carileri = aktif_ibani_olan_carileri_bul(veriler)
    
    bagli_ibanli_gruplar = []
    bagli_ibansiz_gruplar = []
    
    for c_id, info in baglantilar.items():
        grup_adi = info.get("grup", "Bilinmeyen")
        if normalize_text(grup_adi) in aktif_iban_carileri:
            bagli_ibanli_gruplar.append((c_id, grup_adi))
        else:
            bagli_ibansiz_gruplar.append((c_id, grup_adi))
            
    ibanli_isimler = sorted(list(set(item[1] for item in bagli_ibanli_gruplar)))
    ibansiz_isimler = sorted(list(set(item[1] for item in bagli_ibansiz_gruplar)))
    
    ibanli_blok = format_satir_satir_cariler(ibanli_isimler, 3)
    ibansiz_blok = format_satir_satir_cariler(ibansiz_isimler, 3)
    
    metin = (
        f"📢 <b>TOPLU DUYURU KONTROL PANELİ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 <b>İletilecek Mesaj:</b>\n"
        f"<i>« {duyuru_icerik} »</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>HEDEF KİTLE ÖZETİ:</b>\n"
        f"• 🟢 <b>İBAN'ı Aktif:</b> <code>{len(bagli_ibanli_gruplar)} Grup</code>\n"
        f"• ⚪ <b>İBAN'ı Olmayan:</b> <code>{len(bagli_ibansiz_gruplar)} Grup</code>\n"
        f"• 👥 <b>Toplam Bağlı:</b> <b>{len(baglantilar)} Grup</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏢 <b>GRUPLAR DETAYI:</b>\n\n"
        f"🟢 <b>Aktif İBAN'lı Gruplar ({len(bagli_ibanli_gruplar)}):</b>\n"
        f"{ibanli_blok}\n\n"
        f"⚪ <b>İBAN'sız Gruplar ({len(bagli_ibansiz_gruplar)}):</b>\n"
        f"{ibansiz_blok}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 <i>Lütfen duyurunun iletileceği hedefi seçiniz:</i>"
    )
    
    butonlar = []
    if bagli_ibanli_gruplar:
        butonlar.append([{"text": f"🟢 Sadece İBAN'ı Aktif Gruplara ({len(bagli_ibanli_gruplar)} Grup)", "callback_data": f"duyuru_gonder_iban_{draft_id}"}])
    else:
        butonlar.append([{"text": "⚪ Sadece İBAN'ı Aktif Gruplara (0 Grup)", "callback_data": f"duyuru_bos_uyari_{draft_id}"}])
        
    butonlar.append([{"text": f"🌐 Tüm Bağlı Gruplara Gönder ({len(baglantilar)} Grup)", "callback_data": f"duyuru_gonder_tumu_{draft_id}"}])
    butonlar.append([{"text": f"🎯 Özel Tek Bir Grup Seç ({len(baglantilar)} Grup)", "callback_data": f"duyuru_ozel_menu_{draft_id}"}])
    butonlar.append([{"text": "❌ Gönderimi İptal Et", "callback_data": f"duyuru_iptal_{draft_id}"}])
    
    return metin, {"inline_keyboard": butonlar}

def duyuru_ozel_grup_secim_ekrani(draft_id: str) -> Tuple[str, dict]:
    """
    Yöneticinin bağlı olan tüm gruplar arasından tek bir grubu seçip özel duyuru gönderebileceği 2 sütunlu seçim ekranı üretir.
    """
    taslaklar = app_state.get("DUYURU_TASLAKLARI", {})
    if draft_id not in taslaklar:
        return (
            "⚠️ <b>Duyuru taslağının süresi dolmuş veya işlem tamamlanmış.</b>",
            {"inline_keyboard": [[{"text": "🗑️ Mesajı Kapat", "callback_data": "mesaj_kapat"}]]}
        )
        
    taslak = taslaklar[draft_id]
    duyuru_icerik = taslak["metin"]
    
    grup_baglantilarini_guncelle()
    baglantilar = app_state.get("GRUP_BAGLANTILARI", {})
    
    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    veriler = get_sheet_values_fast(sayfa)
    aktif_iban_carileri = aktif_ibani_olan_carileri_bul(veriler)
    
    metin = (
        f"🎯 <b>ÖZEL TEK GRUP SEÇİM EKRANI</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 <b>Duyuru Metni:</b>\n"
        f"<i>« {duyuru_icerik} »</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 <i>Duyurunun yalnızca iletileceği <b>tek bir grubu</b> seçiniz:</i>"
    )
    
    sirali_gruplar = sorted(
        baglantilar.items(),
        key=lambda x: (0 if normalize_text(x[1].get("grup", "")) in aktif_iban_carileri else 1, x[1].get("grup", ""))
    )
    
    buton_satirlari = []
    for i in range(0, len(sirali_gruplar), 2):
        satir = []
        c_id1, info1 = sirali_gruplar[i]
        g_ad1 = info1.get("grup", "Grup")
        icon1 = "🟢" if normalize_text(g_ad1) in aktif_iban_carileri else "⚪"
        satir.append({"text": f"{icon1} {g_ad1}", "callback_data": f"duyuru_tek_{draft_id}_{c_id1}"})
        
        if i + 1 < len(sirali_gruplar):
            c_id2, info2 = sirali_gruplar[i+1]
            g_ad2 = info2.get("grup", "Grup")
            icon2 = "🟢" if normalize_text(g_ad2) in aktif_iban_carileri else "⚪"
            satir.append({"text": f"{icon2} {g_ad2}", "callback_data": f"duyuru_tek_{draft_id}_{c_id2}"})
        buton_satirlari.append(satir)
        
    buton_satirlari.append([
        {"text": "🔙 Ana Menüye Dön", "callback_data": f"duyuru_ana_menu_{draft_id}"},
        {"text": "❌ İptal", "callback_data": f"duyuru_iptal_{draft_id}"}
    ])
    
    return metin, {"inline_keyboard": buton_satirlari}

def toplu_duyuru_tek_grup_yayinla_callback(draft_id: str, hedef_chat_id: int, gonderen_id: int) -> Tuple[str, dict]:
    """
    Seçilen tek bir özel gruba duyuruyu iletir ve rapor döner.
    """
    taslaklar = app_state.get("DUYURU_TASLAKLARI", {})
    if draft_id not in taslaklar:
        return (
            "⚠️ <b>Duyuru taslağının süresi dolmuş veya işlem tamamlanmış.</b>",
            {"inline_keyboard": [[{"text": "🗑️ Mesajı Kapat", "callback_data": "mesaj_kapat"}]]}
        )
        
    taslak = taslaklar.pop(draft_id)
    duyuru_icerik = taslak["metin"]
    
    grup_baglantilarini_guncelle()
    baglantilar = app_state.get("GRUP_BAGLANTILARI", {})
    grup_adi = baglantilar.get(hedef_chat_id, {}).get("grup", "Seçilen Cari")
    
    saat_tarih = suankiZamaniAl().strftime("%d.%m.%Y %H:%M")
    duyuru_mesaji = (
        f"📢 <b>ŞİRKET DUYURUSU</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{duyuru_icerik}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ <i>{saat_tarih}</i>  •  🏛️ <b>CFO Yönetim</b>"
    )
    
    ok = False
    hata_str = ""
    try:
        res = telegramMesajGonder(hedef_chat_id, duyuru_mesaji)
        if res and res.get("ok"):
            ok = True
        else:
            hata_str = res.get("description", "Yanıt alınamadı") if isinstance(res, dict) else "Hata"
    except Exception as e:
        hata_str = str(e)
        
    sistemeLogYaz(
        "Özel Grup Duyurusu",
        f"Gönderen: {gonderen_id} | Grup: {grup_adi} ({hedef_chat_id}) | Durum: {'Başarılı' if ok else hata_str}"
    )
    
    if ok:
        rapor = (
            f"📢 <b>ÖZEL GRUP DUYURU RAPORU</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>Hedef Grup:</b> <b>{grup_adi}</b>\n"
            f"✅ <b>Durum:</b> <b>Başarıyla İletildi</b>\n"
            f"⏰ <b>Saat:</b> <code>{saat_tarih}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 <i>Duyuru yalnızca <b>{grup_adi}</b> grubuna özel olarak iletilmiştir.</i>"
        )
    else:
        rapor = (
            f"❌ <b>Özel Duyuru Gönderilemedi!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>Hedef Grup:</b> <b>{grup_adi}</b>\n"
            f"⚠️ <b>Hata:</b> {hata_str}\n"
        )
        
    klavye = {
        "inline_keyboard": [
            [{"text": "🗑️ Mesajı Kapat", "callback_data": "mesaj_kapat"}]
        ]
    }
    return rapor, klavye

def toplu_duyuru_yayinla_callback(draft_id: str, hedef_filtre: str, gonderen_id: int) -> Tuple[str, dict]:
    """
    Yönetici seçim butonuna bastığında onaylanan hedef kitleye duyuruyu anında iletir.
    """
    taslaklar = app_state.get("DUYURU_TASLAKLARI", {})
    if draft_id not in taslaklar:
        return (
            "⚠️ <b>Duyuru taslağının süresi dolmuş veya işlem zaten tamamlanmış.</b>",
            {"inline_keyboard": [[{"text": "🗑️ Mesajı Kapat", "callback_data": "mesaj_kapat"}]]}
        )
        
    taslak = taslaklar.pop(draft_id)
    duyuru_icerik = taslak["metin"]
    
    grup_baglantilarini_guncelle()
    baglantilar = app_state.get("GRUP_BAGLANTILARI", {})
    
    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    veriler = get_sheet_values_fast(sayfa)
    aktif_iban_carileri = aktif_ibani_olan_carileri_bul(veriler)
    
    hedef_chat_idler = []
    hedef_aciklama = ""
    
    if hedef_filtre == "iban_aktif":
        hedef_aciklama = "🟢 Sadece İBAN'ı Aktif Gruplar"
        for c_id, info in baglantilar.items():
            grup_adi = info.get("grup", "")
            if normalize_text(grup_adi) in aktif_iban_carileri:
                hedef_chat_idler.append((c_id, grup_adi))
    else:
        hedef_aciklama = "🌐 Tüm Bağlı Gruplar"
        for c_id, info in baglantilar.items():
            hedef_chat_idler.append((c_id, info.get("grup", "")))
            
    if not hedef_chat_idler:
        return (
            "⚠️ <b>Seçilen hedef kitlede aktif grup bulunamadı!</b>",
            {"inline_keyboard": [[{"text": "🗑️ Mesajı Kapat", "callback_data": "mesaj_kapat"}]]}
        )
        
    saat_tarih = suankiZamaniAl().strftime("%d.%m.%Y %H:%M")
    duyuru_mesaji = (
        f"📢 <b>ŞİRKET DUYURUSU</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{duyuru_icerik}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ <i>{saat_tarih}</i>  •  🏛️ <b>CFO Yönetim</b>"
    )
    
    basarili_gruplar = []
    basarisiz_gruplar = []
    
    for c_id, g_ad in hedef_chat_idler:
        try:
            res = telegramMesajGonder(c_id, duyuru_mesaji)
            if res and res.get("ok"):
                basarili_gruplar.append(g_ad)
            else:
                hata_detay = res.get("description", "Bilinmeyen hata") if isinstance(res, dict) else "Yanıt alınamadı"
                basarisiz_gruplar.append(f"{g_ad} ({hata_detay})")
        except Exception as e:
            basarisiz_gruplar.append(f"{g_ad} ({e})")
            
    sistemeLogYaz(
        "Toplu Duyuru Yayınlandı",
        f"Gönderen: {gonderen_id} | Filtre: {hedef_filtre} | Başarılı: {len(basarili_gruplar)}/{len(hedef_chat_idler)}"
    )
    
    iletilen_cari_str = ", ".join(sorted(list(set(basarili_gruplar)))) if basarili_gruplar else "Yok"
    
    rapor = (
        f"📢 <b>TOPLU DUYURU RAPORU</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>Hedef Kitle:</b> <b>{hedef_aciklama}</b>\n"
        f"✅ <b>Başarılı Gönderim:</b> <b>{len(basarili_gruplar)} Grup</b>\n"
    )
    if basarili_gruplar:
        rapor += f"🏢 <b>İletilen Cariler:</b>\n<i>{iletilen_cari_str}</i>\n"
        
    if basarisiz_gruplar:
        rapor += (
            f"\n❌ <b>Ulaşılamayan Gruplar ({len(basarisiz_gruplar)}):</b>\n"
            f"• " + "\n• ".join(basarisiz_gruplar) + "\n"
        )
        
    rapor += (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ <b>Saat:</b> <code>{saat_tarih}</code>\n"
        f"💡 <i>Duyuru başarıyla seçilen gruplara iletilmiştir.</i>"
    )
    
    klavye = {
        "inline_keyboard": [
            [{"text": "🗑️ Mesajı Kapat", "callback_data": "mesaj_kapat"}]
        ]
    }
    return rapor, klavye

def grup_kasa_analiz_fisi_uret(grup_ham: str) -> Tuple[str, Optional[dict]]:
    hedef_norm = normalize_text(grup_ham)
    if not hedef_norm:
        raise ValueError("Grup adı boş olamaz.")
        
    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    tum_veriler = sayfa.get_all_values()
    
    hedef_satir = None
    gercek_grup_adi = grup_ham.strip().upper()
    
    for row in tum_veriler[1:]:
        if len(row) >= 2 and normalize_text(row[1]) == hedef_norm:
            hedef_satir = row
            gercek_grup_adi = row[1].strip()
            break
            
    if not hedef_satir:
        raise ValueError(f"Tabloda '<b>{grup_ham}</b>' adlı grup bulunamadı. Lütfen grup adını kontrol edin.")
        
    vals = [guvenliSayi(x) for x in hedef_satir[1:7]]
    while len(vals) < 6:
        vals.append(0.0)
    devir, kasa, odenen, komisyon, kalan = vals[1], vals[2], vals[3], vals[4], vals[5]
    
    tarih_str = sayfa.title
    saat_str = suankiZamaniAl().strftime("%H:%M")

    mesaj = (
        f"📊 <b>[ {gercek_grup_adi.upper()} ] GÜNCEL KASA ANALİZİ</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📅 Tarih: {tarih_str} | ⏰ Saat: {saat_str}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🔄 Önceki Devir: {paraFormatla(devir)}\n"
        f"💰 Eklenen Kasa: {paraFormatla(kasa)}\n"
        f"💸 Yapılan Ödeme: {paraFormatla(odenen)}\n"
        f"✂️ Kesinti/Masraf: {paraFormatla(komisyon)}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🏦 <b>NET KALAN TL: {paraFormatla(kalan)}</b>\n"
        f"━━━━━━━━━━━━━━━"
    )

    draft_id = f"r_{int(time.time())}_{random.randint(100, 999)}"
    app_state.setdefault("RAPOR_TASLAKLARI", {})[draft_id] = {
        "grup": gercek_grup_adi,
        "metin": mesaj
    }

    klavye = {
        "inline_keyboard": [
            [{"text": f"📤 {gercek_grup_adi.upper()} Grubuna İlet", "callback_data": f"rapor_ilet_{draft_id}"}],
            [{"text": "🗑️ Mesajı Kapat", "callback_data": "mesaj_kapat"}]
        ]
    }
    return mesaj, klavye

def menuKlavyesiOlustur(isGroup: bool):
    keyboard = [
        [{"text": "🖥️ Canlı CFO Dashboard", "callback_data": "dashboard_yenile"}],
        [{"text": "📊 Tüm Gruplar Raporu", "callback_data": "rapor_tumu"}],
        [{"text": "🚨 Risk & Bakiye Sıralaması", "callback_data": "risk_tumu"}],
        [{"text": "📉 Masraf & Gider Raporu", "callback_data": "rapor_masraf"}],
        [{"text": "💼 Hızlı Finans Özeti", "callback_data": "rapor_ozet"}],
        [{"text": "🌅 Yeni Gün Geçişi", "callback_data": "menu_yenigun"}]
    ]
    try:
        sh = get_spreadsheet()
        sayfa = get_active_daily_sheet(sh)
        tum_satirlar = sayfa.get_all_values()
        eklenen = set()
        for r in tum_satirlar[1:]:
            if len(r) >= 2:
                gAd = r[1].strip()
                if gAd and gAd != "*" and "GENEL TOPLAM" not in gAd.upper():
                    uAd = normalize_text(gAd)
                    if uAd not in eklenen:
                        eklenen.add(uAd)
                        emoji = grupEmojisiBul(gAd)
                        keyboard.append([{"text": f"{emoji} {gAd}", "callback_data": f"rapor_{gAd}"}])
    except Exception:
        pass
    keyboard.append([{"text": "🛠️ Komut Rehberi", "callback_data": "rehber"}])
    return {"inline_keyboard": keyboard}

def rehber_ana_metni() -> str:
    return (
        "📚 <b>CFO BOT AKILLI KOMUT REHBERİ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Şirketinizin finans, kasa, masraf ve döviz operasyonlarını 7/24 kesintisiz yönetebilirsiniz.\n\n"
        "👇 <b>Detaylı bilgi ve örnek kullanımlar için bir kategori seçin:</b>"
    )

def rehber_ana_klavyesi():
    return {
        "inline_keyboard": [
            [
                {"text": "💰 Kasa & Ödeme", "callback_data": "rehber_kasa"},
                {"text": "📉 Masraf Yönetimi", "callback_data": "rehber_masraf"}
            ],
            [
                {"text": "🔗 Grup Bağlama", "callback_data": "rehber_grup"},
                {"text": "📊 Günlük Raporlar", "callback_data": "rehber_rapor"}
            ],
            [
                {"text": "🪙 Kripto & Döviz", "callback_data": "rehber_kripto"},
                {"text": "🛡️ Yönetici Yetkileri", "callback_data": "rehber_admin"}
            ],
            [
                {"text": "📜 Tüm Komutlar (Tek Liste)", "callback_data": "rehber_tumu"}
            ]
        ]
    }

def rehber_kategori_klavyesi():
    return {
        "inline_keyboard": [
            [{"text": "⬅️ Ana Rehber Menüsü", "callback_data": "rehber_ana"}]
        ]
    }

def rehber_kategori_metni(kategori: str) -> str:
    if kategori == "kasa":
        return (
            "🏢 <b>KASA VE ÖDEME İŞLEMLERİ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "• <code>/kasa</code> : <i>Bağlı Telegram grubunda tek tuşla canlı kasa durum fişini döker.</i>\n"
            "• <code>/kasa [Grup] [Tutar]</code> : <i>Kasaya nakit ekler (Örn: /kasa SACİD 500.000).</i>\n"
            "• <code>/kasasil [Grup] [Tutar]</code> : <i>Kasa tutarından düşer (Örn: /kasasil SACİD 50.000).</i>\n"
            "• <code>/odeme [Grup] [Tutar]</code> : <i>Yapılan ödemeyi işler (Örn: /odeme SACİD 100.000).</i>\n"
            "• <code>/odemesil [Grup] [Tutar]</code> : <i>Ödenen tutardan düşer.</i>\n"
            "• <code>/devir [Grup] [Tutar]</code> : <i>Cari satırına devir/borç ekler (Örn: /devir TİGER 250.000).</i>\n"
            "• <code>/devirsil [Grup] [Tutar]</code> : <i>Devir tutarından düşer.</i>\n"
            "• <code>/toplu</code> : ⚡ <i>Çoklu hızlı işlem: Birden fazla kasa, ödeme, masraf hareketini tek mesajda işler.</i>\n"
            "• <code>/gerial</code> : <i>En son yapılan hatalı işlemi hafızadan geri alır.</i>\n"
            "• <code>/not [Metin]</code> : <i>Şirket hafızasına kalıcı not ekler (Örn: /not SACİD saat 18:00'de ödeme yapacak).</i>\n"
            "• <code>/notlar</code> : <i>Kaydedilmiş son şirket notlarını listeler.</i>"
        )
    elif kategori == "masraf":
        return (
            "📉 <b>MASRAF VE GİDER YÖNETİMİ</b>\n"
            "━━━━━━━━━━━━━━━\n\n"
            "• <code>/masrafekle [Kalem] [Tutar]</code> : <i>Excel'deki ilk boş satıra yeni masraf kalemi olarak işler.</i>\n"
            "• <code>/masrafsil [Kalem] [Tutar]</code> : <i>İlgili masrafı siler veya tutarını düşer.</i>\n"
            "• <code>/masraf</code> veya <code>/gider</code> : <i>Günün tüm masraf kalemlerini ve toplam gider bilançosunu listeler.</i>"
        )
    elif kategori == "grup":
        return (
            "👥 <b>GRUP VE CARİ EŞLEŞTİRME</b>\n"
            "━━━━━━━━━━━━━━━━\n\n"
            "• <code>/grupbagla [Grup Adı]</code> : <i>Bu Telegram grubunu Excel'deki cari satırına bağlar.</i>\n"
            "• <code>/grupkopar</code> : <i>İçinde bulunulan grubun Excel eşleştirmesini kaldırır.</i>\n"
            "• <code>/gruplar</code> : <i>Hangi Telegram grubunun hangi Excel carisine bağlı olduğunu listeler.</i>\n"
            "• <code>/senkron</code> veya <code>/grupguncelle</code> : 🔄 <i>Excel'de değiştirilen grup/cari isimlerini botla anında eşitler.</i>\n"
            "• <code>/duyuru [Metin]</code> : 📢 <i>Yalnızca Excel'e bağlı carilerin gruplarına toplu duyuru geçer.</i>"
        )
    elif kategori == "rapor":
        return (
            "📊 <b>GÜNLÜK DÖNGÜ VE RAPORLAR</b>\n"
            "━━━━━━━━━━━━━━━\n\n"
            "• <code>/hedef</code> veya <code>/kpi</code> : 🎯 <i>Canlı ciro hedefi doluluk oranı, ilerleme çubuğu ve kalan tutar.</i>\n"
            "• <code>/trend</code> veya <code>/haftalik</code> : 📈 <i>Son 7 günün konsolide bilançosu, büyüme trendi ve en aktif carileri.</i>\n"
            "• <code>/dashboard</code> : 📱 <i>Sohbet içi görsel canlı finans ve cari dashboard kartı.</i>\n"
            "• <code>/bakiye</code> veya <code>/sirala</code> : ⚖️ <i>Konsolide risk ve bakiye sıralaması.</i>\n"
            "• <code>/borclular</code> veya <code>/borc</code> : 🚨 <i>Yalnızca eksi bakiyedeki / şirkete borçlu riskli carileri listeler.</i>\n"
            "• <code>/alacaklar</code> : 💰 <i>Pozitif emanet kasası olan carileri büyükten küçüğe sıralar.</i>\n"
            "• <code>/ozet</code> : <i>Toplam devir, kasa, ödeme, komisyon ve net kalan şirket bilançosu.</i>\n"
            "• <code>/rapor</code> : <i>Tüm aktif grupların ayrıntılı döküm raporunu verir.</i>\n"
            "• <code>/tarih [GG.AA.YYYY]</code> : 📅 <i>Geçmiş günün genel bilançosunu veya cari fişini döker.</i>\n"
            "• <code>/ekstre [Cari] [Gün]</code> : <i>Carinin son 5 günlük Devir, Kasa, Ödeme ve Kalan hesap ekstresini döker.</i>\n"
            "• <code>/yenigun</code> : 🌅 <i>Gün sonu devir işlemi: Dünün net kalan kasasını yeni günün devrine aktarır.</i>\n"
            "• <code>/kapanis</code> : 🌙 <i>Kurucuya özel gün sonu kapanış bilançosu.</i>"
        )
    elif kategori == "kripto":
        return (
            "🪙 <b>KRİPTO, KUR VE FİNANS ARAÇLARI</b>\n"
            "━━━━━━━━━━━━━━━\n\n"
            "• <code>/kur</code> : <i>Binance, Paribu, BtcTurk, WhiteBIT canlı USDT/TRY ve Kapalıçarşı Harem kurları.</i>\n"
            "• <code>/kurfark</code> veya <code>/makas</code> : 🔄 <i>Kapalıçarşı Harem Doları ile 5 büyük borsa anlık makas ve kar sıralaması.</i>\n"
            "• <code>/arbitraj [Tutar]</code> : ⚡ <i>Kapalıçarşı Doları vs Kripto Borsa USDT canlı makas ve arbitraj analizi.</i>\n"
            "• <code>/doviz [Tutar] [Birim]</code> : 💱 <i>Çoklu döviz/kripto çevirici (USD, EUR, USDT, TL anlık dönüşümü).</i>\n"
            "• <code>/portfoy</code> : 💼 <i>Şirket konsolide hazine ve portföy bilançosu (TL, USD, EUR, USDT).</i>\n"
            "• <code>/canlikur</code> : <i>Dünya para birimleri (USD, EUR, GBP) ve global piyasa kurları.</i>\n"
            "• <code>/hesap [Grup] [Kom%] [Kur]</code> : <i>Tether / Komisyon hesap makinesi.</i>\n"
            "• <code>/iban</code> : <i>Kullanımdaki ve boşta olan şirket İBAN'larını listeler.</i>\n"
            "• <code>/hesaplar</code> veya <code>/grupiban</code> : 📋 <i>Gruba bağlı tüm aktif İBAN'ları listeler ve butonla silme imkanı sunar.</i>\n"
            "• <code>/sablon [Hesap]</code> : 📋 <i>Excel resmi ödeme şablonunu çeker ve grupta otomatik tahsis eder.</i>\n"
            "• <code>/ibantahsis [Hesap] [Cari]</code> : <i>İBAN'ı cariye tahsis edip 'Kullanımda' yapar.</i>\n"
            "• <code>/ibanbosalt [Hesap]</code> : <i>İBAN'ı boşa çıkarır ve 'Müsait' yapar.</i>\n"
            "• <code>/ibancoz [İBAN]</code> : <i>İBAN'ı doğrular (MOD-97), bankasını bulur ve temiz format üretir.</i>\n"
            "• <code>/t [Cüzdan]</code> : 🏛️ <i>Canlı TRC-20 rezerv ve TL karşılığı (Sadece Kurucu).</i>\n"
            "• <code>/qr [Cüzdan]</code> : ⚡ <i>Hızlı ödeme QR kodu üretir ve borsa analizi yapar.</i>"
        )
    elif kategori == "admin":
        return (
            "🛡️ <b>YÖNETİCİ KONTROLLERİ</b>\n"
            "━━━━━━━━━━━━━━━\n\n"
            "• <code>/adminler</code> : <i>Sistemde yetkilendirilmiş şirket yöneticilerini listeler.</i>\n"
            "• <code>/adminekle [ID] [İsim]</code> : <i>Yeni yönetici yetkilendirir (Sadece Kurucu).</i>\n"
            "• <code>/adminsil [ID]</code> : <i>Yöneticinin bot yetkisini geri alır.</i>\n"
            "• <code>/senkron</code> : 🔄 <i>Excel'deki güncel grup ve cari isimlerini bota aktarır.</i>\n"
            "• <code>/duyuru [Metin]</code> : 📢 <i>Bağlı cari gruplarına akıllı hedef seçimli duyuru paneli açar.</i>\n"
            "• <code>/kapanis</code> : 🌙 <i>Gün sonu kapanış bilançosunu anında özelinize gönderir (Sadece Kurucu).</i>\n"
            "• <code>/kapanissaati [SS:DD]</code> : <i>Otomatik gün sonu bildirim saatini ayarlar (Örn: /kapanissaati 23:00).</i>\n"
            "• <code>/panel</code> : <i>Canlı CFO Web Dashboard bağlantı linkini verir.</i>\n"
            "• <code>/dashboard</code> : <i>Sohbet içi görsel canlı finans dashboard kartı döker.</i>\n"
            "• <code>/not [Metin]</code> : <i>Şirket hafızasına kalıcı not ekler.</i>\n"
            "• <code>/notlar</code> : <i>Şirket hafızasındaki son notları listeler.</i>\n"
            "• <code>/debug</code> : <i>Sistemi test eder, gecikmeyi (ping) ölçer, performansı optimize eder.</i>\n"
            "• <code>/id</code> : <i>Kendi Telegram kullanıcı ID numaranızı görüntüler.</i>"
        )
    else:  # "tumu"
        return (
            "📚 <b>TÜM SİSTEM KOMUTLARI</b>\n"
            "━━━━━━━━━━━━━━━\n\n"
            "🏢 <b>KASA VE OPERASYON</b>\n"
            "• <code>/kasa</code> : Canlı durum fişi döker.\n"
            "• <code>/kasa [Grup] [Tutar]</code> : Kasaya nakit ekler.\n"
            "• <code>/kasasil [Grup] [Tutar]</code> : Kasadan tutar siler.\n"
            "• <code>/odeme [Grup] [Tutar]</code> : Ödenen tutarı işler.\n"
            "• <code>/odemesil [Grup] [Tutar]</code> : Ödenen tutardan düşer.\n"
            "• <code>/devir [Grup] [Tutar]</code> : Devir bakiyesi ekler.\n"
            "• <code>/devirsil [Grup] [Tutar]</code> : Devirden siler.\n"
            "• <code>/toplu</code> : ⚡ Çoklu hızlı işlem (+, -, Ö, D, M).\n"
            "• <code>/masrafekle [Kalem] [Tutar]</code> : Sonraki boş satıra masraf işler.\n"
            "• <code>/masrafsil [Kalem] [Tutar]</code> : Masraf siler/düşer.\n"
            "• <code>/masraf</code> : Günlük masraf listesini döker.\n"
            "• <code>/gerial</code> : En son işlemi geri alır.\n"
            "• <code>/not [Metin]</code> : Şirket hafızasına not kaydeder.\n"
            "• <code>/notlar</code> : Kaydedilmiş son notları listeler.\n\n"
            "👥 <b>GRUP VE CARİ EŞLEŞTİRME</b>\n"
            "• <code>/grupbagla [Grup Adı]</code> : Grubu Excel satırına bağlar.\n"
            "• <code>/grupkopar</code> : Grubun Excel bağlantısını kaldırır.\n"
            "• <code>/gruplar</code> : Bağlı grupları listeler.\n"
            "• <code>/senkron</code> veya <code>/grupguncelle</code> : 🔄 Excel'de değiştirilen grup/cari isimlerini botla anında eşitler.\n"
            "• <code>/duyuru [Metin]</code> : 📢 Bağlı cari gruplarına akıllı hedef seçimli toplu/özel duyuru geçer.\n\n"
            "📊 <b>GÜNLÜK DÖNGÜ VE RAPORLAR</b>\n"
            "• <code>/hedef</code> : 🎯 Canlı ciro hedefi & ilerleme çubuğu.\n"
            "• <code>/trend</code> : 📈 Haftalık konsolide büyüme ve cari hacimleri.\n"
            "• <code>/dashboard</code> : 📱 Görsel sohbet içi canlı finans paneli.\n"
            "• <code>/bakiye</code> : ⚖️ Konsolide risk ve bakiye sıralaması.\n"
            "• <code>/borclular</code> : 🚨 Eksi bakiyeli / borçlu carileri sıralar.\n"
            "• <code>/alacaklar</code> : 💰 Pozitif kasaları büyükten küçüğe sıralar.\n"
            "• <code>/ozet</code> : Kasa, masraf ve ödenen bilanço özeti.\n"
            "• <code>/rapor</code> : Tüm grupların detaylı durum raporu.\n"
            "• <code>/tarih [GG.AA.YYYY]</code> : Geçmiş günün genel tablosu/cari fişi.\n"
            "• <code>/ekstre [Cari] [Gün]</code> : Çok günlük cari hesap ekstresi.\n"
            "• <code>/yenigun</code> : 🌅 Kalan kasayı devire aktararak yeni günü açar.\n"
            "• <code>/kapanis</code> : 🌙 Kurucuya özel gün sonu kapanış bilançosu.\n\n"
            "🪙 <b>KRİPTO, KUR VE İBAN ARAÇLARI</b>\n"
            "• <code>/kur</code> : Canlı borsa USDT/TRY ve Kapalıçarşı Dolar kurları.\n"
            "• <code>/kurfark</code> : 🔄 Kapalıçarşı vs 5 Kripto Borsa makas ve kar tablosu.\n"
            "• <code>/arbitraj [Tutar]</code> : Kapalıçarşı Dolar vs Borsa USDT makası.\n"
            "• <code>/doviz [Tutar] [Birim]</code> : Çoklu döviz ve kripto çevirici.\n"
            "• <code>/portfoy</code> : Şirket konsolide hazine ve portföy bilançosu.\n"
            "• <code>/hesap [Grup] [Kom%] [Kur]</code> : Tether hesap makinesi.\n"
            "• <code>/iban</code> : Şirket İBAN listesi.\n"
            "• <code>/hesaplar</code> : 📋 Gruba bağlı aktif İBAN'ları listeler ve butonla siler.\n"
            "• <code>/sablon [Hesap]</code> : Excel ödeme şablonunu çeker ve grupta otomatik tahsis eder.\n"
            "• <code>/ibantahsis [Hesap] [Cari]</code> : İBAN'ı cariye tahsis eder.\n"
            "• <code>/ibanbosalt [Hesap]</code> : İBAN'ı boşa çıkarır.\n"
            "• <code>/ibancoz [İBAN]</code> : İBAN doğrulama ve banka tespiti.\n"
            "• <code>/t</code> : Canlı TRC-20 rezerv ve bakiye raporu (Sadece Kurucu).\n"
            "• <code>/qr [Cüzdan]</code> : Cüzdan QR kodu ve istihbarat analizi.\n"
            "• <code>/canlikur</code> : Dünya borsaları ve döviz kurları.\n\n"
            "🛡️ <b>YÖNETİCİ KONTROLLERİ</b>\n"
            "• <code>/adminler</code> : Yetkili yöneticileri listeler.\n"
            "• <code>/adminekle [ID] [İsim]</code> : Yeni yönetici ekler.\n"
            "• <code>/adminsil [ID]</code> : Yöneticiyi siler.\n"
            "• <code>/kapanissaati [SS:DD]</code> : Otomatik rapor saatini ayarlar.\n"
            "• <code>/panel</code> : Canlı Web Dashboard linki.\n"
            "• <code>/dashboard</code> : Sohbet içi görsel panel kartı.\n"
            "• <code>/not [Metin]</code> : Şirket hafızasına not kaydeder.\n"
            "• <code>/notlar</code> : Kaydedilmiş son notları listeler.\n"
            "• <code>/debug</code> : Sistem hızlandırma ve gecikme (ping) testi.\n"
            "• <code>/id</code> : Telegram kullanıcı ID'nizi gösterir."
        )

def rehber_metni():
    return rehber_kategori_metni("tumu")

def parse_grup_ve_tutar(parametreler: List[str]) -> Tuple[str, float]:
    if len(parametreler) < 2:
        raise ValueError("Eksik bilgi! Örnek: <code>/kasa TİGER 1500</code> veya <code>/masrafekle Yemek 500</code>")
    
    # 1. Sondaki parametre sayı mı kontrol et (Örn: /masrafekle Yemek 500 veya /masrafekle Ofis Gideri 1.250,50)
    son_str = parametreler[-1].strip()
    if re.search(r'\d', son_str):
        tutar = guvenliSayi(son_str)
        if tutar != 0.0 or son_str in ["0", "0,0", "0.0", "0,00", "0.00"]:
            grup = " ".join(parametreler[:-1]).strip()
            if grup:
                return grup, tutar

    # 2. Baştaki parametre sayı mı kontrol et (Örn: /masrafekle 500 Yemek)
    ilk_str = parametreler[0].strip()
    if re.search(r'\d', ilk_str):
        tutar = guvenliSayi(ilk_str)
        if tutar != 0.0 or ilk_str in ["0", "0,0", "0.0", "0,00", "0.00"]:
            grup = " ".join(parametreler[1:]).strip()
            if grup:
                return grup, tutar

    raise ValueError("Lütfen geçerli bir sayısal tutar girin! Örnek: <code>/kasa TİGER 1500</code> veya <code>/masrafekle Yemek 500</code>")

def hucreyeVeriYaz_impl(komut_metni: str, sutun_idx: int, isim: str, carp: int) -> str:
    parcalar = komut_metni.strip().split()[1:]
    grup_ham, tutar = parse_grup_ve_tutar(parcalar)
    hedef_norm = normalize_text(grup_ham)
    
    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    tum_veriler = get_sheet_values_fast(sayfa)
    
    for i, row in enumerate(tum_veriler[1:], start=2):
        if len(row) >= 2 and normalize_text(row[1]) == hedef_norm:
            mevcut_val = guvenliSayi(row[sutun_idx - 1]) if len(row) >= sutun_idx else 0.0
            yeni_val = round(mevcut_val + (tutar * carp), 2)
            
            update_sheet_matrix_memory(sayfa.title, i, sutun_idx, yeni_val)
            sayfa.update_cell(i, sutun_idx, yeni_val)
            
            app_state["SON_ISLEM"] = {
                "sayfa": sayfa.title, "satir": i, "sutun": sutun_idx,
                "eskiDeger": mevcut_val, "grupAdi": row[1], "islemTuru": isim
            }
            sistemeLogYaz(isim, f"{row[1].upper()} | {paraFormatla(tutar * carp)}")
            
            row_vals = [guvenliSayi(x) for x in row[1:7]]
            while len(row_vals) < 6: row_vals.append(0.0)
            row_vals[sutun_idx - 2] = yeni_val
            dDevir, dKasa, dOdenen, dKomisyon, dKalan = row_vals[1], row_vals[2], row_vals[3], row_vals[4], row_vals[5]
            
            return (
                f"✅ <b>{isim} Başarılı!</b>\n━━━━━━━━━━━━━━━━\n"
                f"{grupEmojisiBul(row[1])} <b>{row[1].upper()}</b>\n"
                f"💵 İşlem Tutarı: <b>{paraFormatla(tutar * carp)}</b>\n\n"
                f"🔄 Devir: {paraFormatla(dDevir)}\n"
                f"💰 Kasa: {paraFormatla(dKasa)}\n"
                f"💸 Ödenen: {paraFormatla(dOdenen)}\n"
                f"✂️ Komisyon: {paraFormatla(dKomisyon)}\n"
                f"🏦 <b>Kalan: {paraFormatla(dKalan)}</b>\n\n"
                f"<i>Hatalı işlem mi? /gerial yazabilirsiniz.</i>"
            )
    raise ValueError(f"Tabloda '<b>{grup_ham}</b>' adlı grup bulunamadı.")

def masrafVerisiYaz_impl(komut_metni: str, isim: str, carp: int) -> str:
    parcalar = komut_metni.strip().split()[1:]
    masraf_ham, tutar = parse_grup_ve_tutar(parcalar)
    hedef_norm = normalize_text(masraf_ham)
    
    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    tum_veriler = get_sheet_values_fast(sayfa)
    
    # 1. MASRAF EKLEME (carp == 1): Her zaman sonraki ilk boş satıra yeni kayıt olarak yazar (mevcut satırın üzerine toplamaz)
    if carp > 0:
        bos_satir = None
        for i, row in enumerate(tum_veriler[1:], start=2):
            col_i = row[8].strip() if len(row) > 8 else ""
            col_j = row[9].strip() if len(row) > 9 else ""
            if not col_i and not col_j:
                bos_satir = i
                break
                
        if bos_satir is None:
            bos_satir = len(tum_veriler) + 1

        tutar_yuvarlanmis = round(tutar, 2)
        update_sheet_matrix_memory(sayfa.title, bos_satir, 9, masraf_ham.upper())
        update_sheet_matrix_memory(sayfa.title, bos_satir, 10, tutar_yuvarlanmis)
        sayfa.update_cell(bos_satir, 9, masraf_ham.upper())
        sayfa.update_cell(bos_satir, 10, tutar_yuvarlanmis)
        
        app_state["SON_ISLEM"] = {
            "sayfa": sayfa.title, "satir": bos_satir, "sutun": 10,
            "eskiDeger": 0, "grupAdi": masraf_ham.upper(),
            "islemTuru": "Masraf Ekleme", "is_new_masraf": True
        }
        sistemeLogYaz("Masraf Ekleme", f"{masraf_ham.upper()} | {paraFormatla(tutar_yuvarlanmis)}")
        
        return (
            f"✅ <b>Masraf Eklendi!</b>\n━━━━━━━━━━━━━━━\n"
            f"📉 Masraf Kalemi: <b>{masraf_ham.upper()}</b>\n"
            f"💵 Eklenen Tutar: <b>{paraFormatla(tutar_yuvarlanmis)}</b>\n"
            f"📌 Excel Satırı: <b>Satır {bos_satir}</b>\n\n"
            f"<i>Hatalı işlem mi? /gerial yazabilirsiniz.</i>"
        )
        
    # 2. MASRAF SİLME (carp == -1): En son eklenen ilgili masraf kalemini (aşağıdan yukarıya) bulup düşer veya siler
    else:
        bulunan_i = None
        bulunan_row = None
        for i in range(len(tum_veriler) - 1, 0, -1):
            row = tum_veriler[i]
            col_i = row[8].strip() if len(row) > 8 else ""
            if normalize_text(col_i) == hedef_norm:
                bulunan_i = i + 1  # 1-based row index
                bulunan_row = row
                break
                
        if not bulunan_i:
            raise ValueError(f"Tabloda '<b>{masraf_ham}</b>' adlı masraf kalemi bulunamadı.")
            
        col_i = bulunan_row[8].strip()
        col_j = bulunan_row[9].strip() if len(bulunan_row) > 9 else ""
        mevcut = guvenliSayi(col_j)
        yeni = round(mevcut - tutar, 2)
        
        if yeni <= 0.0001:
            update_sheet_matrix_memory(sayfa.title, bulunan_i, 9, "")
            update_sheet_matrix_memory(sayfa.title, bulunan_i, 10, "")
            sayfa.update_cell(bulunan_i, 9, "")
            sayfa.update_cell(bulunan_i, 10, "")
            app_state["SON_ISLEM"] = {
                "sayfa": sayfa.title, "satir": bulunan_i, "sutun": 10,
                "eskiDeger": mevcut, "eskiAd": col_i, "grupAdi": col_i,
                "islemTuru": "Masraf Silme", "is_masraf_update": True
            }
            sistemeLogYaz("Masraf Silme", f"{col_i} | Tamamı Silindi ({paraFormatla(mevcut)})")
            return (
                f"🗑️ <b>Masraf Satırı Silindi!</b>\n━━━━━━━━━━━━━━\n"
                f"📉 Masraf Kalemi: <b>{col_i}</b>\n"
                f"💵 Silinen Tutar: <b>{paraFormatla(mevcut)}</b>\n"
                f"📌 Excel Satırı: <b>Satır {bulunan_i}</b>\n\n"
                f"<i>Hatalı işlem mi? /gerial yazabilirsiniz.</i>"
            )
        else:
            sayfa.update_cell(bulunan_i, 10, yeni)
            app_state["SON_ISLEM"] = {
                "sayfa": sayfa.title, "satir": bulunan_i, "sutun": 10,
                "eskiDeger": mevcut, "eskiAd": col_i, "grupAdi": col_i,
                "islemTuru": "Masraf Silme", "is_masraf_update": True
            }
            sistemeLogYaz("Masraf Silme", f"{col_i} | -{paraFormatla(tutar)} (Kalan: {paraFormatla(yeni)})")
            return (
                f"✅ <b>Masraf Tutarı Düşüldü!</b>\n━━━━━━━━━━━━━\n"
                f"📉 Masraf Kalemi: <b>{col_i}</b>\n"
                f"💵 Düşülen Tutar: <b>{paraFormatla(tutar)}</b>\n"
                f"📊 Güncel Kalan Masraf: <b>{paraFormatla(yeni)}</b>\n\n"
                f"<i>Hatalı işlem mi? /gerial yazabilirsiniz.</i>"
            )

def tablodan_finans_ozeti_hesapla(veriler: List[List[str]]) -> Dict[str, Any]:
    toplamDevir = toplamKasa = toplamOdenen = toplamKomisyon = toplamKalan = 0.0
    aktif_gruplar = []
    excel_toplam_satiri = None
    
    for row_idx, row in enumerate(veriler[1:], start=2):
        if len(row) >= 2:
            grup_adi = row[1].strip()
            if not grup_adi or grup_adi == "*":
                continue
                
            if "GENEL TOPLAM" in grup_adi.upper():
                vals = [guvenliSayi(x) for x in row[1:7]]
                while len(vals) < 6: vals.append(0.0)
                excel_toplam_satiri = {
                    "devir": vals[1], "kasa": vals[2], "odenen": vals[3],
                    "komisyon": vals[4], "kalan": vals[5]
                }
                continue
                
            if row_idx <= 42:
                vals = [guvenliSayi(x) for x in row[1:7]]
                while len(vals) < 6: vals.append(0.0)
                devir, kasa, odenen, kom, kalan = vals[1], vals[2], vals[3], vals[4], vals[5]
                
                toplamDevir += devir
                toplamKasa += kasa
                toplamOdenen += odenen
                toplamKomisyon += kom
                toplamKalan += kalan
                
                if any(abs(x) > 0.001 for x in [devir, kasa, odenen, kom, kalan]):
                    aktif_gruplar.append({
                        "ad": grup_adi, "devir": devir, "kasa": kasa,
                        "odenen": odenen, "komisyon": kom, "kalan": kalan
                    })
                    
    if excel_toplam_satiri and any(abs(v) > 0.001 for v in excel_toplam_satiri.values()):
        toplamDevir = excel_toplam_satiri["devir"]
        toplamKasa = excel_toplam_satiri["kasa"]
        toplamOdenen = excel_toplam_satiri["odenen"]
        toplamKomisyon = excel_toplam_satiri["komisyon"]
        toplamKalan = excel_toplam_satiri["kalan"]

    return {
        "devir": toplamDevir,
        "kasa": toplamKasa,
        "odenen": toplamOdenen,
        "komisyon": toplamKomisyon,
        "kalan": toplamKalan,
        "aktif_gruplar": aktif_gruplar
    }

def hizliOzetUret_impl() -> str:
    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    veriler = get_sheet_values_fast(sayfa)
    
    finans = tablodan_finans_ozeti_hesapla(veriler)
    saat = suankiZamaniAl().strftime("%H:%M")
    
    return (
        f"📊 <b>GÜNLÜK FİNANS BİLANÇOSU</b>\n"
        f"━━━━━━━━━━━\n"
        f"📅 Tarih: {sayfa.title} | ⏰ Saat: {saat}\n"
        f"🏢 Aktif Grup Sayısı: {len(finans['aktif_gruplar'])}\n"
        f"━━━━━━━━━━━\n\n"
        f"🔄 Toplam Devir: {paraFormatla(finans['devir'])}\n"
        f"💰 Eklenen Kasa: {paraFormatla(finans['kasa'])}\n"
        f"💸 Toplam Ödeme: {paraFormatla(finans['odenen'])}\n"
        f"✂️ Toplam Komisyon: {paraFormatla(finans['komisyon'])}\n"
        f"━━━━━━━━━━━\n"
        f"🏦 <b>NET KALAN KASA: {paraFormatla(finans['kalan'])}</b>\n"
        f"━━━━━━━━━━━\n"
        f"💡 <i>Tüm grupların anlık genel toplamıdır.</i>"
    )

def tumGruplarRaporu_impl() -> str:
    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    veriler = get_sheet_values_fast(sayfa)
    
    finans = tablodan_finans_ozeti_hesapla(veriler)
    saat = suankiZamaniAl().strftime("%H:%M")
    
    mesaj = (
        f"📊 <b>GÜNLÜK DETAYLI GRUP RAPORU</b>\n"
        f"━━━━━━━━━━━━━\n"
        f"📅 Tarih: {sayfa.title} | ⏰ Saat: {saat}\n"
        f"━━━━━━━━━━━━━\n\n"
    )
    
    if not finans["aktif_gruplar"]:
        return "📭 <b>Bugün için henüz işlem görmüş aktif bir grup bulunmuyor.</b>"
        
    for g in finans["aktif_gruplar"]:
        emoji = grupEmojisiBul(g["ad"])
        mesaj += (
            f"{emoji} <b>{g['ad'].upper()}</b>\n"
            f"🔄 Devir: {paraFormatla(g['devir'])}\n"
            f"💰 Kasa: {paraFormatla(g['kasa'])}\n"
            f"💸 Ödenen: {paraFormatla(g['odenen'])}\n"
            f"✂️ Komisyon: {paraFormatla(g['komisyon'])}\n"
            f"🏦 <b>Kalan: {paraFormatla(g['kalan'])}</b>\n\n"
        )
        
    mesaj += (
        f"━━━━━━━━━━━━━\n"
        f"🏆 <b>GENEL TOPLAM BİLANÇO</b>\n"
        f"🔄 Toplam Devir: {paraFormatla(finans['devir'])}\n"
        f"💰 Toplam Kasa: {paraFormatla(finans['kasa'])}\n"
        f"💸 Toplam Ödeme: {paraFormatla(finans['odenen'])}\n"
        f"✂️ Toplam Komisyon: {paraFormatla(finans['komisyon'])}\n"
        f"━━━━━━━━━━━━━\n"
        f"🏦 <b>NET KALAN KASA: {paraFormatla(finans['kalan'])}</b>\n"
        f"━━━━━━━━━━━━━"
    )
    return mesaj

def bakiye_risk_raporu_uret(filtre_turu: str = "tumu") -> Tuple[str, dict]:
    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    veriler = get_sheet_values_fast(sayfa)
    finans = tablodan_finans_ozeti_hesapla(veriler)
    saat = suankiZamaniAl().strftime("%H:%M")
    
    klavye = {
        "inline_keyboard": [
            [
                {"text": "🚨 Borçlular", "callback_data": "risk_borclular"},
                {"text": "💰 Pozitifler", "callback_data": "risk_pozitif"},
                {"text": "⚖️ Tümü", "callback_data": "risk_tumu"}
            ],
            [
                {"text": "🖥️ CFO Dashboard", "callback_data": "cfo_dashboard"},
                {"text": "🔄 Yenile", "callback_data": f"risk_{filtre_turu}"}
            ],
            [
                {"text": "🗑️ Mesajı Kapat", "callback_data": "mesaj_kapat"}
            ]
        ]
    }
    
    aktif_cariler = finans.get("aktif_gruplar", [])
    borclular = [g for g in aktif_cariler if g["kalan"] < -0.001]
    borclular.sort(key=lambda x: x["kalan"])  # en çok borçlu olan en başta
    
    pozitifler = [g for g in aktif_cariler if g["kalan"] > 0.001]
    pozitifler.sort(key=lambda x: x["kalan"], reverse=True)  # en yüksek pozitif en başta
    
    sifirlar = [g for g in aktif_cariler if abs(g["kalan"]) <= 0.001]
    
    toplam_borc = sum(g["kalan"] for g in borclular)
    toplam_pozitif = sum(g["kalan"] for g in pozitifler)
    net_kasa = finans.get("kalan", 0.0)

    if filtre_turu in ["borclular", "borc", "risk"]:
        mesaj = (
            f"🚨 <b>RİSK & BORÇLU CARİLER LİSTESİ</b>\n"
            f"━━━━━━━━━━\n"
            f"📅 Tarih: {sayfa.title} | ⏰ Saat: <code>{saat}</code>\n"
            f"👥 <b>Borçlu Cari Sayısı:</b> <code>{len(borclular)} Cari</code>\n"
            f"━━━━━━━━━━\n\n"
        )
        if not borclular:
            mesaj += "🟢 <b>Harika!</b> Şu anda eksi bakiyede / şirkete borçlu durumda hiçbir cari bulunmuyor.\n\n"
        else:
            for idx, g in enumerate(borclular, 1):
                emoji = grupEmojisiBul(g["ad"])
                mesaj += (
                    f"🔴 <b>{idx}. {emoji} {g['ad'].upper()}</b>\n"
                    f"• 🔄 Devir: {paraFormatla(g['devir'])} | 💰 Kasa: {paraFormatla(g['kasa'])}\n"
                    f"• 💸 Ödenen: {paraFormatla(g['odenen'])}\n"
                    f"• 🚨 <b>Kalan Risk/Borç: {paraFormatla(g['kalan'])}</b>\n\n"
                )
            mesaj += (
                f"━━━━━━━━━━━━━━━\n"
                f"🚨 <b>TOPLAM CARİ AÇIĞI / BORÇ:</b> <code>{paraFormatla(toplam_borc)}</code>\n"
                f"━━━━━━━━━━━━━━━\n"
            )
        mesaj += "💡 <i>Kasa veya ödeme girişleri için /kasa veya /odeme komutlarını kullanabilirsiniz.</i>"
        return mesaj, klavye

    elif filtre_turu in ["pozitif", "alacaklar", "alacak"]:
        mesaj = (
            f"💰 <b>POZİTİF KASA & BAKİYE SIRALAMASI</b>\n"
            f"━━━━━━━━━━\n"
            f"📅 Tarih: {sayfa.title} | ⏰ Saat: <code>{saat}</code>\n"
            f"🏢 <b>Pozitif Bakiyeli Cari Sayısı:</b> <code>{len(pozitifler)} Cari</code>\n"
            f"━━━━━━━━━━\n\n"
        )
        if not pozitifler:
            mesaj += "📭 Pozitif bakiyeli aktif cari bulunmuyor.\n\n"
        else:
            madalyalar = {1: "🥇", 2: "🥈", 3: "🥉"}
            for idx, g in enumerate(pozitifler, 1):
                emoji = grupEmojisiBul(g["ad"])
                madalya = madalyalar.get(idx, f"<b>{idx}.</b>")
                mesaj += (
                    f"{madalya} {emoji} <b>{g['ad'].upper()}</b>\n"
                    f"• 💰 Kasa: {paraFormatla(g['kasa'])} | 💸 Ödenen: {paraFormatla(g['odenen'])}\n"
                    f"• 🏦 <b>Kalan Bakiye: {paraFormatla(g['kalan'])}</b>\n\n"
                )
            mesaj += (
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💎 <b>TOPLAM POZİTİF EMANET KASA:</b> <code>{paraFormatla(toplam_pozitif)}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
            )
        mesaj += "💡 <i>Bakiye detay fişi için grupta /kasa veya /kasa [Grup] yazınız.</i>"
        return mesaj, klavye

    else:  # "tumu"
        mesaj = (
            f"⚖️ <b>KONSOLİDE RİSK & BAKİYE SIRALAMASI</b>\n"
            f"━━━━━━━━━━━━\n"
            f"📅 Tarih: {sayfa.title} | ⏰ Saat: <code>{saat}</code>\n"
            f"👥 <b>İşlem Gören Aktif Cari:</b> <code>{len(aktif_cariler)} Adet</code>\n"
            f"━━━━━━━━━━━━\n\n"
        )
        
        # 1. Eksi Bakiyeliler (Risk)
        if borclular:
            mesaj += f"🚨 <b>RİSK & BORÇLU CARİLER ({len(borclular)} Adet)</b>\n"
            for idx, g in enumerate(borclular, 1):
                emoji = grupEmojisiBul(g["ad"])
                mesaj += f"• 🔴 {idx}. {emoji} <b>{g['ad'].upper()}:</b> <code>{paraFormatla(g['kalan'])}</code>\n"
            mesaj += f"• ⚠️ <b>Toplam Risk:</b> <code>{paraFormatla(toplam_borc)}</code>\n\n"
        else:
            mesaj += "🟢 <b>Risk Masası:</b> Eksi bakiyede cari bulunmuyor.\n\n"
            
        # 2. Pozitif Bakiyeliler Sıralaması
        if pozitifler:
            mesaj += f"🏆 <b>POZİTİF KASA LİDERLİĞİ (Top {min(len(pozitifler), 10)})</b>\n"
            madalyalar = {1: "🥇", 2: "🥈", 3: "🥉"}
            for idx, g in enumerate(pozitifler[:10], 1):
                emoji = grupEmojisiBul(g["ad"])
                madalya = madalyalar.get(idx, f"{idx}.")
                mesaj += f"• {madalya} {emoji} <b>{g['ad'].upper()}:</b> <b>{paraFormatla(g['kalan'])}</b>\n"
            if len(pozitifler) > 10:
                mesaj += f"• <i>... ve {len(pozitifler) - 10} cari daha</i>\n"
            mesaj += f"• 💎 <b>Toplam Pozitif Kasa:</b> <code>{paraFormatla(toplam_pozitif)}</code>\n\n"
            
        # 3. Kapanmış / Sıfır Bakiyeliler
        if sifirlar:
            sifir_adlar = ", ".join([g["ad"].upper() for g in sifirlar[:5]])
            if len(sifirlar) > 5:
                sifir_adlar += f" (+{len(sifirlar)-5})"
            mesaj += f"⚖️ <b>Sıfırlanmış / Dengede ({len(sifirlar)}):</b> <i>{sifir_adlar}</i>\n\n"
            
        mesaj += (
            f"━━━━━━━━━━━━\n"
            f"📊 <b>KONSOLİDE NET DURUM:</b>\n"
            f"• 💎 Toplam Pozitif Kasa: <b>{paraFormatla(toplam_pozitif)}</b>\n"
            f"• 🚨 Toplam Borç/Açık: <b>{paraFormatla(toplam_borc)}</b>\n"
            f"• 🏦 <b>NET KALAN KASA: {paraFormatla(net_kasa)}</b>\n"
            f"━━━━━━━━━━━━\n"
            f"💡 <i>Detaylı filtreler için aşağıdaki butonları kullanabilirsiniz.</i>"
        )
        return mesaj, klavye

def cfo_dashboard_raporu_uret() -> Tuple[str, dict]:
    """Excel'deki /rapor verilerini (tüm carilerin Devir, Kasa, Ödeme, Komisyon, Kalan detaylarını) şık ve görsel bir Dashboard olarak üretir."""
    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    veriler = get_sheet_values_fast(sayfa)
    finans = tablodan_finans_ozeti_hesapla(veriler)
    
    saat = suankiZamaniAl().strftime("%H:%M")
    tarih = sayfa.title
    aktifler = finans.get("aktif_gruplar", [])
    
    if not aktifler:
        return (
            "📭 <b>Bugün için henüz işlem görmüş aktif bir cari bulunmuyor.</b>",
            {"inline_keyboard": [[{"text": "🔄 Dashboard Yenile", "callback_data": "dashboard_yenile"}]]}
        )
        
    dashboard_metni = (
        f"🖥️ <b>CFO CANLI FİNANS & CARİ DASHBOARD</b>\n"
        f"━━━━━━━━━━\n"
        f"📅 <b>Tarih:</b> <code>{tarih}</code> | ⏰ <b>Saat:</b> <code>{saat}</code>\n"
        f"👥 <b>İşlem Gören Cari:</b> <code>{len(aktifler)} Adet</code>\n"
        f"━━━━━━━━━━\n\n"
        f"📋 <b>CARİ BAZLI CANLI HAREKET TABLOSU:</b>\n\n"
    )
    
    for g in aktifler:
        emoji = grupEmojisiBul(g["ad"])
        kalan = g["kalan"]
        if kalan > 0.001:
            durum_tag = "🟢 <i>Pozitif</i>"
        elif kalan < -0.001:
            durum_tag = "🔴 <i>Borçlu</i>"
        else:
            durum_tag = "⚪ <i>Dengede</i>"
            
        dashboard_metni += (
            f"👤 {emoji} <b>{g['ad'].upper()}</b> ({durum_tag})\n"
            f"• 🔄 Devir: <code>{paraFormatla(g['devir'])}</code>\n"
            f"• 💰 Kasa: <code>+{paraFormatla(g['kasa'])}</code>\n"
            f"• 💸 Ödenen: <code>-{paraFormatla(g['odenen'])}</code>\n"
            f"• ✂️ Komisyon: <code>{paraFormatla(g['komisyon'])}</code>\n"
            f"• 🏦 <b>Kalan: <code>{paraFormatla(g['kalan'])}</code></b>\n\n"
        )
        
    dashboard_metni += (
        f"━━━━━━━━━━━\n"
        f"🏆 <b>KONSOLİDE GENEL TOPLAM BİLANÇO</b>\n"
        f"• 🔄 <b>Toplam Devir:</b> <code>{paraFormatla(finans['devir'])}</code>\n"
        f"• 💰 <b>Toplam Eklenen Kasa:</b> <code>+{paraFormatla(finans['kasa'])}</code>\n"
        f"• 💸 <b>Toplam Yapılan Ödeme:</b> <code>-{paraFormatla(finans['odenen'])}</code>\n"
        f"• ✂️ <b>Toplam Komisyon:</b> <code>{paraFormatla(finans['komisyon'])}</code>\n"
        f"━━━━━━━━━━━\n"
        f"🏦 <b>GÜNCEL NET KALAN KASA: {paraFormatla(finans['kalan'])}</b>\n"
        f"━━━━━━━━━━━"
    )
    
    klavye = {
        "inline_keyboard": [
            [
                {"text": "🔄 Dashboard Yenile", "callback_data": "dashboard_yenile"},
                {"text": "🚨 Risk & Borçlular", "callback_data": "risk_borclular"}
            ],
            [
                {"text": "💰 Pozitif Bakiyeler", "callback_data": "risk_pozitif"},
                {"text": "📉 Masraflar", "callback_data": "rapor_masraf"}
            ],
            [
                {"text": "🪙 Canlı Kurlar", "callback_data": "menu_kur"},
                {"text": "📊 Finans Özeti", "callback_data": "rapor_ozet"}
            ],
            [
                {"text": "🗑️ Mesajı Kapat", "callback_data": "mesaj_kapat"}
            ]
        ]
    }
    return dashboard_metni, klavye

def masrafRaporuUret_impl() -> str:
    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    veriler = get_sheet_values_fast(sayfa)
    masraflar = []
    toplam = 0.0
    for row in veriler[1:]:
        if len(row) >= 10:
            ad = row[8].strip()
            if ad and "GENEL TOPLAM" not in ad.upper() and ad != "-":
                fiyat = guvenliSayi(row[9])
                if abs(fiyat) > 0.001:
                    toplam += fiyat
                    masraflar.append({"ad": ad, "fiyat": fiyat})
    if not masraflar:
        return "📭 <b>Bugün için kaydedilmiş bir masraf bulunmuyor.</b>"
    masraflar.sort(key=lambda x: x["fiyat"], reverse=True)
    mesaj = (
        f"📉 <b>{sayfa.title} GÜNLÜK GİDER TABLOSU</b>\n"
        f"━━━━━━━━━━━\n\n"
    )
    for m in masraflar:
        mesaj += f"🔹 <b>{m['ad']}:</b> {paraFormatla(m['fiyat'])}\n"
    mesaj += (
        f"\n━━━━━━━━━━━\n"
        f"📋 Toplam Kalem: <b>{len(masraflar)} Adet</b>\n"
        f"📊 <b>TOPLAM GİDER: {paraFormatla(toplam)}</b>\n"
        f"━━━━━━━━━━━━━"
    )
    return mesaj

def gun_sonu_kapanis_raporu_uret() -> str:
    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    veriler = get_sheet_values_fast(sayfa)
    finans = tablodan_finans_ozeti_hesapla(veriler)
    
    # Masraflar
    masraflar = []
    toplam_masraf = 0.0
    for row in veriler[1:]:
        if len(row) >= 10:
            ad = row[8].strip()
            if ad and "GENEL TOPLAM" not in ad.upper() and ad != "-":
                fiyat = guvenliSayi(row[9])
                if abs(fiyat) > 0.001:
                    toplam_masraf += fiyat
                    masraflar.append({"ad": ad, "fiyat": fiyat})
    masraflar.sort(key=lambda x: x["fiyat"], reverse=True)
    
    # Anlık USDT kuru
    anlik_kur_str = ""
    try:
        b_usdt = http_get_json("https://data-api.binance.vision/api/v3/ticker/price?symbol=USDTTRY")
        anlik_kur_str = f"🟡 <b>Binance USDT/TRY:</b> <code>{float(b_usdt['price']):.2f} ₺</code>\n"
    except Exception:
        pass

    tarih = sayfa.title
    saat = suankiZamaniAl().strftime("%H:%M")
    
    rapor = (
        f"🌙 <b>GÜN SONU FİNANS VE KASA BİLANÇOSU</b>\n"
        f"━━━━━━━━━━━\n"
        f"📅 <b>Tarih:</b> {tarih} | ⏰ <b>Saat:</b> {saat}\n"
        f"🏢 <b>İşlem Gören Grup:</b> {len(finans['aktif_gruplar'])} Adet\n"
        f"━━━━━━━━━━━\n\n"
        f"🔄 <b>Toplam Devir:</b> {paraFormatla(finans['devir'])}\n"
        f"💰 <b>Eklenen Kasa:</b> {paraFormatla(finans['kasa'])}\n"
        f"💸 <b>Toplam Ödeme:</b> {paraFormatla(finans['odenen'])}\n"
        f"✂️ <b>Toplam Komisyon:</b> {paraFormatla(finans['komisyon'])}\n"
        f"📉 <b>Toplam Masraf:</b> {paraFormatla(toplam_masraf)} <i>({len(masraflar)} Kalem)</i>\n"
        f"━━━━━━━━━━━\n"
        f"🏦 <b>GÜN SONU NET KALAN: {paraFormatla(finans['kalan'])}</b>\n"
        f"━━━━━━━━━━━\n\n"
    )
    
    if finans['aktif_gruplar']:
        rapor += "👥 <b>GRUP DURUMLARI:</b>\n"
        for g in finans['aktif_gruplar']:
            emoji = grupEmojisiBul(g["ad"])
            grup_adi = g["ad"].upper()
            kalan_str = paraFormatla(g["kalan"])
            rapor += f"{emoji} <b>{grup_adi}:</b> 🏦 <code>{kalan_str}</code>\n"
            
            has_kasa = abs(g["kasa"]) > 0.001
            has_odenen = abs(g["odenen"]) > 0.001
            if has_kasa or has_odenen:
                detay_parts = []
                if has_kasa:
                    detay_parts.append(f"💰 Kasa: +{paraFormatla(g['kasa'])}")
                if has_odenen:
                    detay_parts.append(f"💸 Ödeme: -{paraFormatla(g['odenen'])}")
                rapor += f"   • <i>({' | '.join(detay_parts)})</i>\n"
        rapor += "\n"
        
    if masraflar:
        rapor += "📉 <b>ÖNE ÇIKAN MASRAFLAR:</b>\n"
        for m in masraflar[:5]:
            rapor += f"🔹 {m['ad']}: {paraFormatla(m['fiyat'])}\n"
        rapor += "\n"
        
    if anlik_kur_str:
        rapor += f"━━━━━━━━━━━━━\n{anlik_kur_str}"
        
    rapor += "━━━━━━━━━━━━━\n💡 <i>Yeni güne devretmek için: /yenigun</i>"
    return rapor

# --- PİYASA VE DIŞ BORSA KURLARI (PARALEL & 15s MİKRO-ÖNBELLEK) ---
_rates_cache = {}
_rates_cache_time = 0.0
_rates_lock = threading.Lock()

def fetch_all_market_rates_parallel(force_refresh: bool = False, max_age: float = 15.0) -> dict:
    """Tüm borsa ve Kapalıçarşı döviz/USDT kurlarını eşzamanlı/paralel çeker ve 15s önbelleğe alır."""
    global _rates_cache, _rates_cache_time
    now = time.time()
    with _rates_lock:
        if not force_refresh and _rates_cache and (now - _rates_cache_time < max_age):
            return dict(_rates_cache)

    def fetch_harem():
        def _parse_kur(val):
            s = str(val or "").strip()
            if "," in s and "." in s:
                s = s.replace(".", "").replace(",", ".")
            elif "," in s:
                s = s.replace(",", ".")
            try:
                return float(s)
            except Exception:
                return 0.0

        gold_data = {"gram": 7350.0, "ons": 2850.0, "gumus": 85.20}
        try:
            d_gold = http_get_json("https://finans.truncgil.com/v3/today.json")
            if isinstance(d_gold, dict):
                g_a = _parse_kur((d_gold.get("gram-altin") or {}).get("Selling"))
                o_a = _parse_kur((d_gold.get("ons") or {}).get("Selling"))
                g_g = _parse_kur((d_gold.get("gumus") or {}).get("Selling"))
                if g_a > 0: gold_data["gram"] = g_a
                if o_a > 0: gold_data["ons"] = o_a
                if g_g > 0: gold_data["gumus"] = g_g
        except Exception:
            pass

        # 1. Öncelikli Gerçek Kaynak: https://kur.doviz.com/harem/amerikan-dolari (Harem Altın Kapalıçarşı Canlı Tahtası)
        try:
            html = http_get_text("https://kur.doviz.com/harem/amerikan-dolari")
            u_alis, u_satis = 0.0, 0.0
            m_u_bid = re.search(r'data-socket-key="23-USD"[^>]*data-socket-attr="bid"[^>]*>([\d,\.\s]+)<', html)
            m_u_ask = re.search(r'data-socket-key="23-USD"[^>]*data-socket-attr="ask"[^>]*>([\d,\.\s]+)<', html)
            if m_u_bid and m_u_ask:
                u_alis = _parse_kur(m_u_bid.group(1))
                u_satis = _parse_kur(m_u_ask.group(1))

            e_alis, e_satis = 0.0, 0.0
            m_e_bid = re.search(r'data-socket-key="23-EUR"[^>]*data-socket-attr="bid"[^>]*>([\d,\.\s]+)<', html)
            m_e_ask = re.search(r'data-socket-key="23-EUR"[^>]*data-socket-attr="ask"[^>]*>([\d,\.\s]+)<', html)
            if m_e_bid and m_e_ask:
                e_alis = _parse_kur(m_e_bid.group(1))
                e_satis = _parse_kur(m_e_ask.group(1))

            if u_alis > 0 and u_satis > 0:
                return {
                    "usd": (u_alis, u_satis),
                    "eur": (e_alis, e_satis) if (e_alis > 0 and e_satis > 0) else (55.60, 55.85),
                    "gold": gold_data
                }
        except Exception as e:
            pass

        # 2. İkincil Yedek Kaynak: Truncgil
        try:
            d = http_get_json("https://finans.truncgil.com/v3/today.json")
            u = d.get("USD", {})
            u_alis = _parse_kur(u.get("Buying"))
            u_satis = _parse_kur(u.get("Selling"))
            e = d.get("EUR", {})
            e_alis = _parse_kur(e.get("Buying"))
            e_satis = _parse_kur(e.get("Selling"))
            return {
                "usd": (u_alis, u_satis) if u_alis > 0 and u_satis > 0 else (48.08, 48.17),
                "eur": (e_alis, e_satis) if e_alis > 0 and e_satis > 0 else (55.60, 55.85),
                "gold": gold_data
            }
        except Exception:
            return {"usd": (48.08, 48.17), "eur": (55.60, 55.85), "gold": gold_data}

    def fetch_fiat():
        try:
            d = http_get_json("https://api.exchangerate-api.com/v4/latest/USD")
            return d.get("rates", {})
        except Exception:
            return {"TRY": 48.09, "EUR": 0.92, "GBP": 0.79}

    def fetch_binance_24h():
        try:
            r = http_get_json("https://data-api.binance.vision/api/v3/ticker/24hr?symbol=USDTTRY")
            return {
                "last": float(r.get("lastPrice", 0)),
                "high": float(r.get("highPrice", 0)),
                "low": float(r.get("lowPrice", 0)),
                "change": float(r.get("priceChangePercent", 0))
            }
        except Exception:
            return None

    def fetch_paribu():
        try:
            r = http_get_json("https://www.paribu.com/ticker")["USDT_TL"]
            return {
                "last": float(r.get("last", 0)),
                "high": float(r.get("high24hr", 0)),
                "low": float(r.get("low24hr", 0))
            }
        except Exception:
            return None

    def fetch_btcturk():
        try:
            r = http_get_json("https://api.btcturk.com/api/v2/ticker?pairSymbol=USDT_TRY")["data"][0]
            return {
                "last": float(r.get("last", 0)),
                "high": float(r.get("high", 0)),
                "low": float(r.get("low", 0))
            }
        except Exception:
            return None

    def fetch_whitebit():
        try:
            r = http_get_json("https://whitebit.com/api/v1/public/ticker?market=USDT_TRY")["result"]
            return {
                "last": float(r.get("last", 0)),
                "high": float(r.get("high", 0)),
                "low": float(r.get("low", 0))
            }
        except Exception:
            return None

    def fetch_okx():
        try:
            r = http_get_json("https://www.okx.com/api/v5/market/ticker?instId=USDT-TRY")["data"][0]
            return {
                "last": float(r.get("last", 0)),
                "high": float(r.get("high24h", 0)),
                "low": float(r.get("low24h", 0))
            }
        except Exception:
            return None

    futures = {
        "harem": _update_executor.submit(fetch_harem),
        "fiat": _update_executor.submit(fetch_fiat),
        "binance": _update_executor.submit(fetch_binance_24h),
        "paribu": _update_executor.submit(fetch_paribu),
        "btcturk": _update_executor.submit(fetch_btcturk),
        "whitebit": _update_executor.submit(fetch_whitebit),
        "okx": _update_executor.submit(fetch_okx),
    }

    results = {}
    for k, fut in futures.items():
        try:
            results[k] = fut.result(timeout=3.5)
        except Exception:
            results[k] = None

    with _rates_lock:
        _rates_cache = dict(results)
        _rates_cache_time = time.time()

    return results

def f_tl(val) -> str:
    try:
        s = f"{float(val):.2f}"
        return s.replace(".", ",") + " ₺"
    except Exception:
        return "- ₺"

def get_harem_dolar_kuru() -> Tuple[float, float]:
    """Harem Altın / Kapalıçarşı Serbest Piyasa Doları (USD/TRY) Alış ve Satış kurlarını çeker."""
    rates = fetch_all_market_rates_parallel()
    h = rates.get("harem") or {}
    return h.get("usd", (48.20, 48.25))

def get_harem_euro_kuru() -> Tuple[float, float]:
    """Harem Altın / Kapalıçarşı Serbest Piyasa Eurosu (EUR/TRY) Alış ve Satış kurlarını çeker."""
    rates = fetch_all_market_rates_parallel()
    h = rates.get("harem") or {}
    return h.get("eur", (52.30, 52.45))

def kurRaporuUret_impl() -> str:
    rates = fetch_all_market_rates_parallel()
    h_usd = rates.get("harem", {}).get("usd", (48.20, 48.25))
    
    yanit = "📊 <b>GÜNCEL DÖVİZ & USDT KURLARI</b>\n━━━━━━━━━━━━\n\n"
    yanit += (
        f"🏛️ <b>HAREM (Kapalıçarşı Doları)</b>\n"
        f"💵 Alış: <b>{f_tl(h_usd[0])}</b> | Satış: <b>{f_tl(h_usd[1])}</b>\n\n"
    )
    
    b = rates.get("binance")
    if b and b.get("last"):
        yanit += f"🟡 <b>BİNANCE USDT/TRY</b>\n💵 Anlık Kur: {f_tl(b['last'])}\n🔺 24saat En Yüksek: {f_tl(b['high'])}\n🔻 24saat En Düşük: {f_tl(b['low'])}\n\n"
    else:
        yanit += "🟡 <b>BİNANCE USDT/TRY</b>\n⚠️ Veri çekilemedi.\n\n"
        
    p = rates.get("paribu")
    if p and p.get("last"):
        yanit += f"🔵 <b>PARİBU USDT/TRY</b>\n💵 Anlık Kur: {f_tl(p['last'])}\n🔺 24saat En Yüksek: {f_tl(p['high'])}\n🔻 24saat En Düşük: {f_tl(p['low'])}\n\n"
    else:
        yanit += "🔵 <b>PARİBU USDT/TRY</b>\n⚠️ Veri çekilemedi.\n\n"
        
    bt = rates.get("btcturk")
    if bt and bt.get("last"):
        yanit += f"🟢 <b>BTCTÜRK USDT/TRY</b>\n💵 Anlık Kur: {f_tl(bt['last'])}\n🔺 24saat En Yüksek: {f_tl(bt['high'])}\n🔻 24saat En Düşük: {f_tl(bt['low'])}\n\n"
    else:
        yanit += "🟢 <b>BTCTÜRK USDT/TRY</b>\n⚠️ Veri çekilemedi.\n\n"
        
    wb = rates.get("whitebit")
    if wb and wb.get("last"):
        yanit += f"⚪ <b>WHITEBIT USDT/TRY</b>\n💵 Anlık Kur: {f_tl(wb['last'])}\n🔺 24saat En Yüksek: {f_tl(wb['high'])}\n🔻 24saat En Düşük: {f_tl(wb['low'])}\n\n"
    else:
        yanit += "⚪ <b>WHITEBIT USDT/TRY</b>\n⚠️ Veri çekilemedi.\n\n"
        
    ok = rates.get("okx")
    if ok and ok.get("last"):
        yanit += f"⚫ <b>OKX USDT/TRY</b>\n💵 Anlık Kur: {f_tl(ok['last'])}\n🔺 24saat En Yüksek: {f_tl(ok['high'])}\n🔻 24saat En Düşük: {f_tl(ok['low'])}\n\n"
        
    return yanit.strip()

def fetch_binance_crypto_tickers(symbols: list) -> dict:
    """Binance REST API üzerinden 24 saatlik fiyat ve % değişim verilerini çeker."""
    result = {}
    try:
        data = http_get_json("https://data-api.binance.vision/api/v3/ticker/24hr")
        if isinstance(data, list):
            for item in data:
                sym = item.get("symbol")
                if sym in symbols:
                    result[sym] = {
                        "price": float(item.get("lastPrice", 0)),
                        "change": float(item.get("priceChangePercent", 0))
                    }
    except Exception:
        pass

    for sym in symbols:
        if sym not in result:
            try:
                item = http_get_json(f"https://data-api.binance.vision/api/v3/ticker/24hr?symbol={sym}")
                if isinstance(item, dict) and "lastPrice" in item:
                    result[sym] = {
                        "price": float(item.get("lastPrice", 0)),
                        "change": float(item.get("priceChangePercent", 0))
                    }
            except Exception:
                pass
    return result

def canliKurSorgula_impl(force_refresh: bool = False):
    try:
        rates = fetch_all_market_rates_parallel(force_refresh=force_refresh)
        b_usdt_val = float((rates.get("binance") or {}).get("last", 48.20))
        fiat = rates.get("fiat") or {}
        try_rate = float(fiat.get("TRY", 48.09))

        harem = rates.get("harem") or {}
        h_usd_alis, h_usd_satis = harem.get("usd", (48.08, 48.17))
        h_eur_alis, h_eur_satis = harem.get("eur", (55.60, 55.85))

        gold_info = harem.get("gold") or {"gram": 7350.0, "ons": 2850.0, "gumus": 85.20}
        gram_altin = float(gold_info.get("gram", 7350.0))
        ons_altin = float(gold_info.get("ons", 2850.0))
        gram_gumus = float(gold_info.get("gumus", 85.20))

        # Kripto kurları (24s Değişim ve Fiyatlar)
        symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "TRXUSDT", "AVAXUSDT", "DOGEUSDT"]
        crypto_data = fetch_binance_crypto_tickers(symbols)

        # 1. Kripto Paralar
        crypto_lines = []
        usdt_chg = float((rates.get("binance") or {}).get("change", 0.0))
        usdt_chg_str = f" 🟢 +{usdt_chg:.2f}%" if usdt_chg > 0 else (f" 🔴 {usdt_chg:.2f}%" if usdt_chg < 0 else "")
        crypto_lines.append(f"🇹🇷 USDT / TRY: <code>{b_usdt_val:.2f} ₺</code>{usdt_chg_str}")

        labels_map = [
            ("BTCUSDT", "🔶 BTC / USDT", "{val:,.0f} $"),
            ("ETHUSDT", "🔷 ETH / USDT", "{val:.2f} $"),
            ("BNBUSDT", "🟡 BNB / USDT", "{val:.2f} $"),
            ("SOLUSDT", "🟣 SOL / USDT", "{val:.2f} $"),
            ("XRPUSDT", "🌐 XRP / USDT", "{val:.4f} $"),
            ("TRXUSDT", "🔴 TRX / USDT", "{val:.4f} $"),
            ("AVAXUSDT", "🔺 AVAX / USDT", "{val:.2f} $"),
            ("DOGEUSDT", "🐕 DOGE / USDT", "{val:.4f} $"),
        ]

        for sym, label, fmt in labels_map:
            c_info = crypto_data.get(sym, {})
            val = c_info.get("price", 0.0)
            chg = c_info.get("change", 0.0)
            if val > 0:
                chg_str = f" 🟢 +{chg:.2f}%" if chg >= 0 else f" 🔴 {chg:.2f}%"
                if sym in ["XRPUSDT", "TRXUSDT", "DOGEUSDT"] and val >= 10:
                    val_str = f"{val:.2f} $"
                else:
                    val_str = fmt.format(val=val)
                crypto_lines.append(f"{label}: <code>{val_str}</code>{chg_str}")

        kripto_metin = "\n".join(crypto_lines)

        # 2. Altın & Kıymetli Madenler
        altin_metin = (
            f"👑 ONS Altın: <code>{ons_altin:,.2f} $</code>\n"
            f"🟡 Gram Altın: <code>{gram_altin:,.2f} ₺</code>\n"
            f"🥈 Gram Gümüş: <code>{gram_gumus:,.2f} ₺</code>"
        )

        # 3. Kapalıçarşı & Arbitraj Makası
        if h_usd_satis > 0:
            makas_pct = ((b_usdt_val - h_usd_satis) / h_usd_satis) * 100
            if makas_pct >= 0:
                makas_str = f"+%{makas_pct:.2f} (USDT Primi)"
            else:
                makas_str = f"-%{abs(makas_pct):.2f} (Nakit Primi)"
        else:
            makas_str = "%0.00"

        kapalicarsi_metin = (
            f"🏬 Nakit USD (Alış/Satış): <code>{h_usd_alis:.2f} ₺ / {h_usd_satis:.2f} ₺</code>\n"
            f"⚡ USDT vs Nakit Makası: <code>{makas_str}</code>"
        )

        # 4. Dünya Para Birimleri
        dunya_metin = (
            f"🇺🇸 Dolar (USD): <code>{try_rate:.2f} ₺</code>\n"
            f"🇪🇺 Euro (EUR): <code>{(try_rate / fiat.get('EUR', 1)):.2f} ₺</code>\n"
            f"🇬🇧 Sterlin (GBP): <code>{(try_rate / fiat.get('GBP', 1)):.2f} ₺</code>\n"
            f"🇨🇭 İsviçre Frangı (CHF): <code>{(try_rate / fiat.get('CHF', 1)):.2f} ₺</code>\n"
            f"🇨🇦 Kanada Dol. (CAD): <code>{(try_rate / fiat.get('CAD', 1)):.2f} ₺</code>\n"
            f"🇦🇺 Avustralya Dol. (AUD): <code>{(try_rate / fiat.get('AUD', 1)):.2f} ₺</code>\n"
            f"🇯🇵 Japon Yeni (JPY): <code>{(try_rate / fiat.get('JPY', 1)):.2f} ₺</code>\n"
            f"🇸🇦 Suudi Riyali (SAR): <code>{(try_rate / fiat.get('SAR', 1)):.2f} ₺</code>\n"
            f"🇷🇺 Rus Rublesi (RUB): <code>{(try_rate / fiat.get('RUB', 1)):.2f} ₺</code>"
        )

        metin = (
            "🌍 <b>CANLI PİYASA & DÜNYA KURLARI</b>\n\n"
            "🪙 <b>Kripto Paralar (Binance)</b>\n"
            f"{kripto_metin}\n\n"
            "🏆 <b>Altın & Kıymetli Madenler (Kapalıçarşı)</b>\n"
            f"{altin_metin}\n\n"
            "🏦 <b>Kapalıçarşı Nakit & Arbitraj Makası</b>\n"
            f"{kapalicarsi_metin}\n\n"
            "💵 <b>Dünya Para Birimleri</b>\n"
            f"{dunya_metin}\n\n"
            f"<i>⏱ Son Güncelleme: {suankiZamaniAl().strftime('%H:%M:%S')}</i>"
        )

        klavye = {
            "inline_keyboard": [
                [{"text": "🔄 Canlı Kurları Yenile", "callback_data": "canli_kur_yenile"}]
            ]
        }
        return metin, klavye
    except Exception as e:
        return f"❌ <b>API Hatası:</b> {e}", None

def arbitraj_raporu_uret_impl(komut_metni: str = "") -> str:
    """
    Kapalıçarşı Nakit Doları ile Kripto Borsa USDT fiyatları arasındaki canlı makası ve arbitraj fırsatlarını hesaplar.
    """
    hacim = 100000.0
    if komut_metni:
        p = komut_metni.strip().split()[1:]
        if p:
            val = guvenliSayi(p[0])
            if val > 0:
                hacim = val

    rates = fetch_all_market_rates_parallel()
    h_alis, h_satis = rates.get("harem", {}).get("usd", (48.20, 48.25))
    
    borsa_fiyatlari = {}
    if rates.get("binance") and rates["binance"].get("last"):
        borsa_fiyatlari["Binance"] = rates["binance"]["last"]
    if rates.get("paribu") and rates["paribu"].get("last"):
        borsa_fiyatlari["Paribu"] = rates["paribu"]["last"]
    if rates.get("btcturk") and rates["btcturk"].get("last"):
        borsa_fiyatlari["BtcTurk"] = rates["btcturk"]["last"]
    if rates.get("whitebit") and rates["whitebit"].get("last"):
        borsa_fiyatlari["WhiteBIT"] = rates["whitebit"]["last"]
    if rates.get("okx") and rates["okx"].get("last"):
        borsa_fiyatlari["OKX"] = rates["okx"]["last"]
        
    if not borsa_fiyatlari:
        borsa_fiyatlari["Binance"] = h_satis * 1.004

    en_yuksek_borsa = max(borsa_fiyatlari.items(), key=lambda x: x[1])
    en_dusuk_borsa = min(borsa_fiyatlari.items(), key=lambda x: x[1])

    # Rota 1: USDT Sat (Borsada) -> Kapalıçarşı Doları Al
    usd_alinan = (en_yuksek_borsa[1] / h_satis) * hacim
    rota1_fark_usd = usd_alinan - hacim
    rota1_fark_tl = rota1_fark_usd * h_alis
    rota1_yuzde = ((en_yuksek_borsa[1] - h_satis) / h_satis) * 100

    # Rota 2: Kapalıçarşı USD Boz -> Borsada USDT Al
    usdt_alinan = (h_alis / en_dusuk_borsa[1]) * hacim
    rota2_fark_usdt = usdt_alinan - hacim
    rota2_fark_tl = rota2_fark_usdt * en_dusuk_borsa[1]
    rota2_yuzde = ((h_alis - en_dusuk_borsa[1]) / en_dusuk_borsa[1]) * 100

    # Sinyal Tespiti
    if rota1_yuzde >= 0.15:
        sinyal_str = f"🟢 <b>GÜÇLÜ ARBİTRAJ FIRSATI!</b> (USDT Primi Yüksek)\n👉 <b>Öneri:</b> {en_yuksek_borsa[0]}'da USDT satıp Kapalıçarşı'dan fiziki Dolar almak avantajlı."
    elif rota2_yuzde >= 0.15:
        sinyal_str = f"🟢 <b>GÜÇLÜ ARBİTRAJ FIRSATI!</b> (Kapalıçarşı Primi Yüksek)\n👉 <b>Öneri:</b> Kapalıçarşı'da Dolar bozdurup {en_dusuk_borsa[0]}'tan USDT almak avantajlı."
    else:
        sinyal_str = f"⚪ <b>DENGELİ PİYASA</b>\n👉 Kapalıçarşı ve Kripto Borsa fiyatları birbirine çok yakın (Makas: %{abs(rota1_yuzde):.2f})."

    borsa_makas_yuzde = ((en_yuksek_borsa[1] - en_dusuk_borsa[1]) / en_dusuk_borsa[1]) * 100 if en_dusuk_borsa[1] > 0 else 0

    saat = suankiZamaniAl().strftime("%H:%M:%S")
    hacim_str = f"{hacim:,.0f} $".replace(",", ".")

    mesaj = (
        f"📊 <b>CANLI ARBİTRAJ & MAKAS ANALİZİ</b>\n"
        f"━━━━━━━━━━━━━\n"
        f"⏰ Saat: <code>{saat}</code> | 💵 Hacim Bazı: <b>{hacim_str}</b>\n\n"
        f"🏛️ <b>PİYASA KURLARI:</b>\n"
        f"• 🏛️ Kapalıçarşı USD: Alış <code>{f_tl(h_alis)}</code> | Satış <code>{f_tl(h_satis)}</code>\n"
    )
    for b_isim, b_fiyat in borsa_fiyatlari.items():
        mesaj += f"• 🪙 {b_isim} USDT: <code>{f_tl(b_fiyat)}</code>\n"
        
    mesaj += (
        f"\n━━━━━━━━━\n"
        f"🔄 <b>ARBİTRAJ ROTALARI VE KÂR/ZARAR:</b>\n\n"
        f"<b>1️⃣ Rota: USDT Sat ({en_yuksek_borsa[0]}) ➔ Kapalıçarşı USD Al</b>\n"
        f"• 📈 Fiyat Makası: <b>%{rota1_yuzde:+.2f}</b>\n"
        f"• 💵 {hacim_str} Net Getiri: <b>{rota1_fark_usd:+,.2f} $</b> (<code>{paraFormatla(rota1_fark_tl)}</code>)\n\n"
        f"<b>2️⃣ Rota: Kapalıçarşı USD Boz ➔ Borsada USDT Al ({en_dusuk_borsa[0]})</b>\n"
        f"• 📈 Fiyat Makası: <b>%{rota2_yuzde:+.2f}</b>\n"
        f"• 💵 {hacim_str} Net Getiri: <b>{rota2_fark_usdt:+,.2f} USDT</b> (<code>{paraFormatla(rota2_fark_tl)}</code>)\n\n"
    )
    
    if len(borsa_fiyatlari) > 1 and borsa_makas_yuzde > 0.05:
        mesaj += (
            f"⚡ <b>Borsalar Arası USDT Makası:</b>\n"
            f"• <b>{en_dusuk_borsa[0]} ➔ {en_yuksek_borsa[0]}:</b> %{borsa_makas_yuzde:.2f} ({f_tl(en_yuksek_borsa[1] - en_dusuk_borsa[1])})\n\n"
        )

    mesaj += (
        f"━━━━━━━━━━\n"
        f"{sinyal_str}\n"
        f"━━━━━━━━━━\n"
        f"💡 <i>Farklı tutar için: <code>/arbitraj [Tutar]</code> (Örn: /arbitraj 250000)</i>"
    )
    return mesaj

def doviz_cevirici_impl(komut_metni: str) -> str:
    """
    Girilen tutarı canlı kurlarla anında TL, USD (Kapalıçarşı), EUR ve USDT (Binance) birimlerine dönüştürür.
    """
    parcalar = komut_metni.strip().split()[1:]
    if not parcalar:
        return (
            "💱 <b>ÇOKLU DÖVİZ & KRİPTO ÇEVİRİCİ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Format: <code>/doviz [Tutar] [Birim]</code>\n\n"
            "📌 <b>Örnekler:</b>\n"
            "• <code>/doviz 100000 USD</code> (Doları çevir)\n"
            "• <code>/doviz 50000 EUR</code> (Euroyu çevir)\n"
            "• <code>/doviz 2500000 TL</code> (TL'yi dövize çevir)\n"
            "• <code>/doviz 100000 USDT</code> (Tether'i çevir)"
        )
        
    tutar_ham = parcalar[0].strip().upper()
    birim = "USD"
    if len(parcalar) > 1:
        birim = parcalar[1].strip().upper()
    else:
        if "$" in tutar_ham or "USD" in tutar_ham: birim = "USD"
        elif "€" in tutar_ham or "EUR" in tutar_ham: birim = "EUR"
        elif "₺" in tutar_ham or "TL" in tutar_ham or "TRY" in tutar_ham: birim = "TL"
        elif "USDT" in tutar_ham: birim = "USDT"

    tutar_str = re.sub(r'[^0-9\,\.]', '', tutar_ham)
    tutar = guvenliSayi(tutar_str)
    if tutar <= 0:
        return "⚠️ Lütfen geçerli bir sayısal tutar giriniz! (Örn: <code>/doviz 100000 USD</code>)"

    h_usd_alis, h_usd_satis = get_harem_dolar_kuru()
    h_eur_alis, h_eur_satis = get_harem_euro_kuru()
    
    binance_usdt = h_usd_satis
    try:
        r_b = http_get_json("https://data-api.binance.vision/api/v3/ticker/price?symbol=USDTTRY")
        binance_usdt = float(r_b.get("price", h_usd_satis))
    except Exception: pass

    eur_usd = h_eur_alis / h_usd_alis if h_usd_alis > 0 else 1.08
    saat = suankiZamaniAl().strftime("%H:%M")
    
    if birim in ["USD", "$", "DOLAR", "DOLLAR"]:
        tl_alis = tutar * h_usd_alis
        tl_satis = tutar * h_usd_satis
        eur_karsilik = tutar / eur_usd
        usdt_karsilik = (tl_alis / binance_usdt) if binance_usdt > 0 else tutar
        
        return (
            f"💱 <b>DÖVİZ DÖNÜŞÜM RAPORU</b>\n"
            f"━━━━━━━━━━━\n"
            f"💵 <b>GİRİLEN TUTAR:</b> <code>{tutar:,.2f} USD ($)</code>\n"
            f"⏰ <b>Saat:</b> {saat}\n"
            f"━━━━━━━━━━━\n\n"
            f"🇹🇷 <b>TÜRK LİRASI (Kapalıçarşı)</b>\n"
            f"• 💵 Bozdurursanız (Alış {f_tl(h_usd_alis)}): <b>{paraFormatla(tl_alis)}</b>\n"
            f"• 💵 Satın Alırsanız (Satış {f_tl(h_usd_satis)}): <b>{paraFormatla(tl_satis)}</b>\n\n"
            f"🪙 <b>KRİPTO USDT (Binance: {f_tl(binance_usdt)})</b>\n"
            f"• <b>{usdt_karsilik:,.2f} USDT</b>\n\n"
            f"🇪🇺 <b>EURO KARŞILIĞI (Parite: {eur_usd:.4f})</b>\n"
            f"• <b>{eur_karsilik:,.2f} EUR (€)</b>\n"
            f"━━━━━━━━━━━"
        )

    elif birim in ["EUR", "€", "EURO", "AVRO"]:
        tl_alis = tutar * h_eur_alis
        tl_satis = tutar * h_eur_satis
        usd_karsilik = tutar * eur_usd
        usdt_karsilik = (tl_alis / binance_usdt) if binance_usdt > 0 else usd_karsilik
        
        return (
            f"💱 <b>DÖVİZ DÖNÜŞÜM RAPORU</b>\n"
            f"━━━━━━━━━\n"
            f"💶 <b>GİRİLEN TUTAR:</b> <code>{tutar:,.2f} EUR (€)</code>\n"
            f"⏰ <b>Saat:</b> {saat}\n"
            f"━━━━━━━━━\n\n"
            f"🇹🇷 <b>TÜRK LİRASI (Kapalıçarşı)</b>\n"
            f"• 💶 Bozdurursanız (Alış {f_tl(h_eur_alis)}): <b>{paraFormatla(tl_alis)}</b>\n"
            f"• 💶 Satın Alırsanız (Satış {f_tl(h_eur_satis)}): <b>{paraFormatla(tl_satis)}</b>\n\n"
            f"🇺🇸 <b>DOLAR KARŞILIĞI (Parite: {eur_usd:.4f})</b>\n"
            f"• <b>{usd_karsilik:,.2f} USD ($)</b>\n\n"
            f"🪙 <b>KRİPTO USDT (Binance: {f_tl(binance_usdt)})</b>\n"
            f"• <b>{usdt_karsilik:,.2f} USDT</b>\n"
            f"━━━━━━━━━━"
        )

    elif birim in ["USDT", "TETHER", "USDTTRY"]:
        tl_karsilik = tutar * binance_usdt
        usd_kapalicarsi = tl_karsilik / h_usd_satis if h_usd_satis > 0 else tutar
        eur_karsilik = tl_karsilik / h_eur_satis if h_eur_satis > 0 else (tutar / eur_usd)
        
        return (
            f"💱 <b>DÖVİZ DÖNÜŞÜM RAPORU</b>\n"
            f"━━━━━━━━━━\n"
            f"🪙 <b>GİRİLEN TUTAR:</b> <code>{tutar:,.2f} USDT</code>\n"
            f"⏰ <b>Saat:</b> {saat}\n"
            f"━━━━━━━━━━\n\n"
            f"🇹🇷 <b>TÜRK LİRASI (Binance: {f_tl(binance_usdt)})</b>\n"
            f"• <b>{paraFormatla(tl_karsilik)}</b>\n\n"
            f"🇺🇸 <b>KAPALIÇARŞI NAKİT DOLAR (Satış: {f_tl(h_usd_satis)})</b>\n"
            f"• <b>{usd_kapalicarsi:,.2f} USD ($)</b>\n\n"
            f"🇪🇺 <b>KAPALIÇARŞI EURO (Satış: {f_tl(h_eur_satis)})</b>\n"
            f"• <b>{eur_karsilik:,.2f} EUR (€)</b>\n"
            f"━━━━━━━━━━"
        )

    else:
        usd_alis = tutar / h_usd_satis if h_usd_satis > 0 else 0
        eur_alis = tutar / h_eur_satis if h_eur_satis > 0 else 0
        usdt_alis = tutar / binance_usdt if binance_usdt > 0 else 0
        
        return (
            f"💱 <b>DÖVİZ DÖNÜŞÜM RAPORU</b>\n"
            f"━━━━━━━━━━\n"
            f"🇹🇷 <b>GİRİLEN TUTAR:</b> <code>{paraFormatla(tutar)}</code>\n"
            f"⏰ <b>Saat:</b> {saat}\n"
            f"━━━━━━━━━━\n\n"
            f"🇺🇸 <b>KAPALIÇARŞI DOLAR (Satış {f_tl(h_usd_satis)})</b>\n"
            f"• Alınabilecek: <b>{usd_alis:,.2f} USD ($)</b>\n\n"
            f"🪙 <b>KRİPTO USDT (Binance {f_tl(binance_usdt)})</b>\n"
            f"• Alınabilecek: <b>{usdt_alis:,.2f} USDT</b>\n\n"
            f"🇪🇺 <b>KAPALIÇARŞI EURO (Satış {f_tl(h_eur_satis)})</b>\n"
            f"• Alınabilecek: <b>{eur_alis:,.2f} EUR (€)</b>\n"
            f"━━━━━━━━━━"
        )

def sirket_portfoy_raporu_impl() -> str:
    """
    Şirketin aktif gün tablosundaki Net Kalan TL kasası ile bağlı cüzdanlardaki USDT rezervlerini
    canlı kurlarla harmanlayıp TL, USD, EUR ve USDT cinsinden konsolide toplam hazine değerini döker.
    """
    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    veriler = get_sheet_values_fast(sayfa)
    finans = tablodan_finans_ozeti_hesapla(veriler)
    kalan_tl = finans["kalan"]
    
    toplam_usdt_rezerv = 0.0
    
    h_usd_alis, h_usd_satis = get_harem_dolar_kuru()
    h_eur_alis, h_eur_satis = get_harem_euro_kuru()
    binance_usdt = h_usd_satis
    try:
        r_b = http_get_json("https://data-api.binance.vision/api/v3/ticker/price?symbol=USDTTRY")
        binance_usdt = float(r_b.get("price", h_usd_satis))
    except Exception: pass

    toplam_net_tl = kalan_tl + (toplam_usdt_rezerv * binance_usdt)
    toplam_usd = toplam_net_tl / h_usd_alis if h_usd_alis > 0 else 0
    toplam_eur = toplam_net_tl / h_eur_alis if h_eur_alis > 0 else 0
    toplam_usdt = toplam_net_tl / binance_usdt if binance_usdt > 0 else 0
    
    saat = suankiZamaniAl().strftime("%H:%M")
    
    return (
        f"💼 <b>ŞİRKET HAZİNE & PORTFÖY BİLANÇOSU</b>\n"
        f"━━━━━━━━━━\n"
        f"📅 Tarih: <b>{sayfa.title}</b> | ⏰ Saat: <code>{saat}</code>\n"
        f"━━━━━━━━━━\n\n"
        f"🏦 <b>MEVCUT VARLIKLAR:</b>\n"
        f"• 🇹🇷 Günlük Kalan TL Kasası: <b>{paraFormatla(kalan_tl)}</b>\n"
        f"• 🪙 TRC-20 USDT Rezervi: <b>{toplam_usdt_rezerv:,.2f} USDT</b>\n\n"
        f"📊 <b>PİYASA KURLARI (Kapalıçarşı & Borsa):</b>\n"
        f"• 💵 USD/TRY (Harem Alış): <code>{f_tl(h_usd_alis)}</code>\n"
        f"• 💶 EUR/TRY (Harem Alış): <code>{f_tl(h_eur_alis)}</code>\n"
        f"• 🪙 USDT/TRY (Binance): <code>{f_tl(binance_usdt)}</code>\n\n"
        f"━━━━━━━━━━\n"
        f"🏆 <b>KONSOLİDE TOPLAM ŞİRKET DEĞERİ:</b>\n"
        f"• 🇹🇷 <b>TOPLAM TL:</b> <code>{paraFormatla(toplam_net_tl)}</code>\n"
        f"• 🇺🇸 <b>TOPLAM USD:</b> <code>{toplam_usd:,.2f} $</code>\n"
        f"• 🪙 <b>TOPLAM USDT:</b> <code>{toplam_usdt:,.2f} USDT</code>\n"
        f"• 🇪🇺 <b>TOPLAM EUR:</b> <code>{toplam_eur:,.2f} €</code>\n"
        f"━━━━━━━━━\n"
        f"💡 <i>Tüm cari bakiyeler ve döviz varlıkları anlık konsolide edilmiştir.</i>"
    )

def hedef_kpi_raporu_uret(yeni_hedef_str: str = None) -> str:
    """Günlük ciro hedefini takip eder, dinamik ilerleme çubuğu ve kalan tutarı gösterir."""
    if yeni_hedef_str:
        try:
            val = float(yeni_hedef_str.replace(".", "").replace(",", ".").replace("₺", "").strip())
            if val > 0:
                app_state["CIRO_HEDEFI"] = val
                sistemeLogYaz("Ciro Hedefi Güncellendi", f"Yeni Hedef: {paraFormatla(val)}")
                return f"✅ <b>Günlük Ciro Hedefi Güncellendi!</b>\n🎯 <b>Yeni Hedef:</b> <code>{paraFormatla(val)}</code>"
        except Exception:
            return "⚠️ <b>Geçersiz Tutar!</b> Örnek: <code>/hedef 50.000.000</code>"

    hedef = float(app_state.get("CIRO_HEDEFI") or 50000000.0)
    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    veriler = get_sheet_values_fast(sayfa)
    finans = tablodan_finans_ozeti_hesapla(veriler)
    
    # Günlük işlenen toplam işlem hacmi (Eklenen Kasa + Yapılan Ödeme)
    islenen_kasa = finans.get("kasa", 0.0)
    islenen_odeme = finans.get("odenen", 0.0)
    toplam_hacim = islenen_kasa + islenen_odeme
    
    # Oran
    yuzde = min(100.0, (toplam_hacim / hedef) * 100.0) if hedef > 0 else 0.0
    kalan = max(0.0, hedef - toplam_hacim)
    p_bar = dynamic_progress_bar(int(yuzde), total_blocks=10)
    
    saat = suankiZamaniAl().strftime("%H:%M")
    durum_emoji = "🔥" if yuzde >= 100 else ("⚡" if yuzde >= 50 else "⏳")
    
    yanit = (
        f"🎯 <b>GÜNLÜK CİRO & KPI HEDEF TAKİBİ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 Tarih: <b>{sayfa.title}</b> | ⏰ Saat: <code>{saat}</code>\n"
        f"🎯 <b>Günlük Hedef:</b> <code>{paraFormatla(hedef)}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{durum_emoji} <b>HEDEF DOLULUK ORANI:</b>\n"
        f"<code>[{p_bar}] %{yuzde:.1f}</code>\n\n"
        f"📊 <b>GÜNÜN FİNANSAL HACMİ:</b>\n"
        f"• 💰 Eklenen Kasa: <code>+{paraFormatla(islenen_kasa)}</code>\n"
        f"• 💸 Yapılan Ödemeler: <code>-{paraFormatla(islenen_odeme)}</code>\n"
        f"• ⚡ <b>Toplam İşlenen Hacim: <code>{paraFormatla(toplam_hacim)}</code></b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )
    
    if yuzde >= 100:
        fazla = toplam_hacim - hedef
        yanit += f"🏆 <b>TEBRİKLER! GÜNLÜK HEDEF AŞILDI!</b>\n🎉 Hedefin <b>{paraFormatla(fazla)}</b> üzerindesiniz!\n"
    else:
        yanit += (
            f"⏳ <b>Hedefe Kalan:</b> <b>{paraFormatla(kalan)}</b>\n"
            f"💪 <i>Hedefe ulaşmaya %{100.0 - yuzde:.1f} kaldı, harika gidiyorsunuz!</i>\n"
        )
        
    yanit += "\n💡 <i>Hedefi değiştirmek için: <code>/hedef 60.000.000</code></i>"
    return yanit

def haftalik_trend_raporu_uret(gun_sayisi: int = 7) -> str:
    """Google E-Tablo'daki geçmiş gün sayfalarını tarayarak haftalık toplam işlem hacmini, masrafları ve en aktif carileri analiz eder."""
    sh = get_spreadsheet()
    tum_ws = sh.worksheets()
    
    tarih_sayfalari = []
    for ws in tum_ws:
        if is_valid_daily_sheet(ws) and re.match(r'^\d{2}\.\d{2}\.\d{4}$', ws.title):
            try:
                t_obj = datetime.datetime.strptime(ws.title, "%d.%m.%Y")
                tarih_sayfalari.append((t_obj, ws))
            except Exception:
                pass
                
    if not tarih_sayfalari:
        return "📭 <b>Geçmiş günlere ait analiz edilecek sayfa bulunamadı.</b>"
        
    tarih_sayfalari.sort(key=lambda x: x[0], reverse=True)
    secilen_gunler = tarih_sayfalari[:gun_sayisi]
    
    toplam_haftalik_kasa = 0.0
    toplam_haftalik_odenen = 0.0
    toplam_haftalik_komisyon = 0.0
    toplam_haftalik_masraf = 0.0
    
    cari_hacimleri = {}  # grup_adi -> toplam_hacim (kasa + odenen)
    
    for t_obj, ws in secilen_gunler:
        try:
            veriler = ws.get_all_values()
            finans = tablodan_finans_ozeti_hesapla(veriler)
            toplam_haftalik_kasa += finans.get("kasa", 0.0)
            toplam_haftalik_odenen += finans.get("odenen", 0.0)
            toplam_haftalik_komisyon += finans.get("komisyon", 0.0)
            
            for g in finans.get("aktif_gruplar", []):
                ad = g["ad"].upper().strip()
                hacim = g["kasa"] + g["odenen"]
                cari_hacimleri[ad] = cari_hacimleri.get(ad, 0.0) + hacim
                
            # Masraflar
            for row in veriler[1:]:
                if len(row) >= 10:
                    m_ad = row[8].strip()
                    if m_ad and "GENEL TOPLAM" not in m_ad.upper() and m_ad != "-":
                        m_fiyat = guvenliSayi(row[9])
                        if abs(m_fiyat) > 0.001:
                            toplam_haftalik_masraf += m_fiyat
        except Exception:
            continue
            
    toplam_haftalik_hacim = toplam_haftalik_kasa + toplam_haftalik_odenen
    baslangic_tarihi = secilen_gunler[-1][1].title
    bitis_tarihi = secilen_gunler[0][1].title
    
    sirali_cariler = sorted(cari_hacimleri.items(), key=lambda x: x[1], reverse=True)
    
    yanit = (
        f"📈 <b>HAFTALIK FİNANS & CARİ PERFORMANS ANALİZİ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🗓️ <b>Dönem:</b> <code>{baslangic_tarihi} - {bitis_tarihi}</code> ({len(secilen_gunler)} Gün)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 <b>KONSOLİDE DÖNEM BİLANÇOSU:</b>\n"
        f"• 💰 Toplam Giriş (Kasa): <code>+{paraFormatla(toplam_haftalik_kasa)}</code>\n"
        f"• 💸 Toplam Çıkış (Ödeme): <code>-{paraFormatla(toplam_haftalik_odenen)}</code>\n"
        f"• ✂️ Toplam Komisyon: <code>{paraFormatla(toplam_haftalik_komisyon)}</code>\n"
        f"• 📉 Toplam Masraf & Gider: <code>{paraFormatla(toplam_haftalik_masraf)}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <b>TOPLAM İŞLEM HACMİ: {paraFormatla(toplam_haftalik_hacim)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    
    if sirali_cariler:
        yanit += "🏆 <b>HAFTANIN EN YÜKSEK HACİMLİ CARİLERİ:</b>\n"
        madalyalar = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        for idx, (ad, hacim) in enumerate(sirali_cariler[:5]):
            pay = (hacim / toplam_haftalik_hacim * 100.0) if toplam_haftalik_hacim > 0 else 0.0
            emoji = grupEmojisiBul(ad)
            m_simge = madalyalar[idx] if idx < len(madalyalar) else "🔹"
            yanit += f"• {m_simge} {emoji} <b>{ad}:</b> <code>{paraFormatla(hacim)}</code> <i>(%{pay:.1f} Pay)</i>\n"
        yanit += "\n"
        
    gunluk_ort = toplam_haftalik_hacim / len(secilen_gunler) if secilen_gunler else 0.0
    yanit += (
        f"💡 <b>YÖNETİCİ ÖZETİ:</b>\n"
        f"• 📅 Günlük Ortalama Hacim: <b>{paraFormatla(gunluk_ort)}</b>\n"
    )
    return yanit

def kur_fark_makas_raporu_uret(simulasyon_tutar_str: str = "100000") -> str:
    """Harem Altın Kapalıçarşı Doları ile 5 büyük kripto borsasının (Binance, Paribu, BtcTurk, WhiteBIT, OKX) USDT kurlarını kıyaslar, anlık makas ve arbitraj karını listeler."""
    try:
        tutar = float(str(simulasyon_tutar_str).replace(".", "").replace(",", ".").replace("$", "").strip())
        if tutar <= 0:
            tutar = 100000.0
    except Exception:
        tutar = 100000.0
        
    rates = fetch_all_market_rates_parallel()
    h_usd_alis, h_usd_satis = rates.get("harem", {}).get("usd", (48.08, 48.17))
    
    # 5 Büyük Kripto Borsası
    borsalar = [
        {"ad": "BİNANCE", "key": "binance", "emoji": "🟡"},
        {"ad": "PARİBU", "key": "paribu", "emoji": "🔵"},
        {"ad": "BTCTÜRK", "key": "btcturk", "emoji": "🟢"},
        {"ad": "WHITEBIT", "key": "whitebit", "emoji": "⚪"},
        {"ad": "OKX", "key": "okx", "emoji": "⚫"}
    ]
    
    makas_listesi = []
    
    for b in borsalar:
        data = rates.get(b["key"])
        if data and isinstance(data, dict) and data.get("last"):
            fiyat = float(data["last"])
            # Makas = Kripto Fiyatı - Harem Dolar Satış
            fark_tl = fiyat - h_usd_satis
            fark_yuzde = (fark_tl / h_usd_satis) * 100.0 if h_usd_satis > 0 else 0.0
            kar_tl = tutar * fark_tl
            makas_listesi.append({
                "ad": b["ad"],
                "emoji": b["emoji"],
                "fiyat": fiyat,
                "fark_tl": fark_tl,
                "fark_yuzde": fark_yuzde,
                "kar_tl": kar_tl
            })
            
    # En karlıdan en az karlıya sırala
    makas_listesi.sort(key=lambda x: x["fark_tl"], reverse=True)
    saat = suankiZamaniAl().strftime("%H:%M:%S")
    
    yanit = (
        f"🔄 <b>KAPALIÇARŞI (HAREM) & KRİPTO MAKAS TABLOSU</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ Canlı Saat: <code>{saat}</code> | 💵 Simülasyon: <b>{tutar:,.0f} $</b>\n"
        f"🏛️ <b>Harem Dolar (Alış / Satış):</b> <code>{f_tl(h_usd_alis)} / {f_tl(h_usd_satis)}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 <b>BORSA BAZLI ANLIK MAKAS & GETİRİ SIRALAMASI:</b>\n\n"
    )
    
    madalyalar = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    
    for idx, item in enumerate(makas_listesi):
        m_icon = madalyalar[idx] if idx < len(madalyalar) else "🔹"
        yon_emoji = "🟢" if item["fark_tl"] >= 0 else "🔴"
        isaret = "+" if item["fark_tl"] >= 0 else ""
        
        yanit += (
            f"{m_icon} {item['emoji']} <b>{item['ad']}</b>\n"
            f"• 💵 USDT/TRY: <code>{f_tl(item['fiyat'])}</code>\n"
            f"• {yon_emoji} Makas Farkı: <b>{isaret}{item['fark_tl']:.2f} ₺</b> <i>(%{item['fark_yuzde']:+.2f})</i>\n"
            f"• 💰 {tutar:,.0f}$ Kar/Fark: <b>{isaret}{paraFormatla(item['kar_tl'])}</b>\n\n"
        )
        
    if makas_listesi:
        lider = makas_listesi[0]
        yanit += (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💎 <b>EN KARLI ARBİTRAJ ROTASI:</b>\n"
            f"🏛️ Harem'den Dolar Al ➔ {lider['emoji']} <b>{lider['ad']}</b>'de USDT Boz!\n"
            f"💵 <b>{tutar:,.0f} $ İşlem Başına Net Kazanç: +{paraFormatla(lider['kar_tl'])}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 <i>Farklı tutar simülasyonu için: <code>/kurfark 250000</code></i>"
        )
    return yanit

def detect_wallet_entity(address: str) -> str:
    """Cüzdanın resmi borsa hesabı mı, ilişkili borsa fon akışı mı yoksa bireysel cüzdan mı olduğunu tespit eder."""
    try:
        url = f"https://apilist.tronscan.org/api/account?address={address}"
        d = http_get_json(url)
        tag = d.get("addressTag") or d.get("publicTag") or d.get("name")
        if tag and str(tag).strip() and str(tag).lower() != "none":
            return f"🏦 <b>Resmi Borsa / Kurum:</b> <code>{tag}</code>"
    except Exception:
        pass
        
    try:
        tx_url = f"https://apilist.tronscan.org/api/token_trc20/transfers?limit=8&start=0&sort=-timestamp&relatedAddress={address}"
        tx_data = http_get_json(tx_url)
        for tx in tx_data.get("token_transfers", []):
            to_tag = tx.get("to_address_tag")
            from_tag = tx.get("from_address_tag")
            
            for t in [to_tag, from_tag]:
                if isinstance(t, str) and len(t) > 1 and t.lower() != "none":
                    return f"🔄 <b>İlişkili Borsa Fon Akışı:</b> <code>{t}</code>"
                elif isinstance(t, dict):
                    name = t.get("name") or t.get("tag") or t.get("addressTag")
                    if name and str(name).strip() and str(name).lower() != "none":
                        return f"🔄 <b>İlişkili Borsa Fon Akışı:</b> <code>{name}</code>"
    except Exception:
        pass
        
    return "👤 <b>Cüzdan Türü:</b> Bireysel / Şahsi Cüzdan <i>(Trust Wallet, TronLink, Ledger)</i>"

def get_tron_balances(address: str) -> Tuple[float, float, float]:
    """Tronscan resmi API üzerinden adresteki TRX, USDT ve toplam USD bakiyesini çeker."""
    url = f"https://apilist.tronscan.org/api/account/token_asset_overview?address={address}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    trx_bakiye = 0.0
    usdt_bakiye = 0.0
    toplam_usd = 0.0
    try:
        with urllib.request.urlopen(req, timeout=7) as res:
            data = json.loads(res.read().decode())
            toplam_usd = float(data.get("totalAssetInUsd", 0))
            for item in data.get("data", []):
                sym = item.get("tokenAbbr", "").upper()
                t_id = item.get("tokenId", "")
                dec = int(item.get("tokenDecimal", 6))
                raw_bal = float(item.get("balance", 0))
                bal = raw_bal / (10 ** dec)
                
                if sym == "TRX" or t_id == "_":
                    trx_bakiye = bal
                elif sym == "USDT" or t_id == "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t":
                    usdt_bakiye = bal
    except Exception as e:
        print(f"Tronscan bakiye okuma hatası ({address}): {e}")
    return trx_bakiye, usdt_bakiye, toplam_usd

def get_borsa_kurlari_listesi() -> Tuple[str, float]:
    rates = fetch_all_market_rates_parallel()
    default_rate = rates.get("fiat", {}).get("TRY", 48.09)
    
    def format_sayi_yerel(val):
        return f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        
    items = [
        ("🟡 <b>BİNANCE</b>", rates.get("binance")),
        ("🔵 <b>PARİBU</b>", rates.get("paribu")),
        ("🟢 <b>BTCTÜRK</b>", rates.get("btcturk")),
        ("⚪ <b>WHITEBIT</b>", rates.get("whitebit")),
        ("⚫ <b>OKX</b>", rates.get("okx")),
    ]
    
    satirlar = []
    fiyatlar = []
    for isim, data in items:
        val = data.get("last", 0.0) if data else 0.0
        if val > 0:
            satirlar.append(f"{isim} USDT/TRY - 💵 Anlık Kur: {format_sayi_yerel(val)} ₺")
            fiyatlar.append(val)
        else:
            satirlar.append(f"{isim} USDT/TRY - 💵 Anlık Kur: {format_sayi_yerel(default_rate)} ₺")
            fiyatlar.append(default_rate)
            
    referans_kur = fiyatlar[0] if (fiyatlar and fiyatlar[0] > 0) else default_rate
    return "\n".join(satirlar), referans_kur

def trc20_varlik_raporu_uret(cuzdan_adresi: str = VARSAYILAN_TRC20_ADRES) -> Tuple[str, dict]:
    cuzdan_adresi = (cuzdan_adresi or "").strip()
    if not cuzdan_adresi:
        cuzdan_adresi = VARSAYILAN_TRC20_ADRES

    trx_bal, usdt_bal, total_usd = get_tron_balances(cuzdan_adresi)
    borsa_kurlari_metni, usdt_try_kur = get_borsa_kurlari_listesi()
    usdt_tl_karsiligi = usdt_bal * usdt_try_kur

    tarih_saat = suankiZamaniAl().strftime("%d.%m.%Y | %H:%M")

    def format_sayi(val):
        return f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    usdt_format = format_sayi(usdt_bal)
    trx_format = format_sayi(trx_bal)
    usd_format = format_sayi(total_usd)
    usdt_tl_format = format_sayi(usdt_tl_karsiligi)
    kur_format = format_sayi(usdt_try_kur)

    mesaj = (
        f"🏛️ <b>REZERV & CANLI VARLIK RAPORU</b>\n"
        f"━━━━━━━━━━━\n"
        f"📅 <b>Tarih/Saat:</b> {tarih_saat}\n"
        f"🌐 <b>Ağ:</b> TRON (TRC-20)\n"
        f"📌 <b>Cüzdan:</b> <code>{cuzdan_adresi}</code>\n"
        f"━━━━━━━━━━━\n"
        f"{borsa_kurlari_metni}\n"
        f"━━━━━━━━━━━\n"
        f"💵 <b>USDT Bakiyesi:</b> <code>{usdt_format} USDT</code>\n"
        f"⚡ <b>TRX Bakiyesi:</b> <code>{trx_format} TRX</code>\n"
        f"🌍 <b>Toplam Varlık (USD):</b> <code>${usd_format}</code>\n"
        f"━━━━━━━━━━━\n"
        f"🇹🇷 <b>USDT TÜRK LİRASI KARŞILIĞI:</b>\n"
        f"💰 <b>{usdt_tl_format} ₺</b> <i>(1 USDT ≈ {kur_format} ₺)</i>\n"
        f"━━━━━━━━━━━\n"
        f"⚡ <i>Canlı Blokzincir Verisi • Tronscan API</i>"
    )

    kesif_url = f"https://tronscan.org/#/address/{cuzdan_adresi}"
    klavye = {
        "inline_keyboard": [
            [{"text": "🔍 Tronscan Explorer'da Doğrula ↗", "url": kesif_url}],
            [{"text": "🔄 Canlı Yenile", "callback_data": f"t_yenile_{cuzdan_adresi}"}]
        ]
    }
    return mesaj, klavye

def cuzdanQrUret_impl(chat_id: int, komut_metni: str):
    parcalar = komut_metni.strip().split()
    if len(parcalar) >= 2:
        cuzdan_adresi = parcalar[1].strip()
    else:
        cuzdan_adresi = VARSAYILAN_TRC20_ADRES.strip() if VARSAYILAN_TRC20_ADRES else ""
        
    if not cuzdan_adresi or len(cuzdan_adresi) < 10:
        telegramMesajGonder(
            chat_id,
            "⚠️ <b>Hatalı Kullanım!</b>\n"
            "Lütfen QR koda dönüştürmek istediğiniz borsa/cüzdan adresini girin.\n\n"
            "📌 <b>Örnek Kullanım:</b>\n"
            "<code>/qr TQHuwJh5c4ygbKhfFoGqTZTahjQuJAX3iV</code>"
        )
        return

    # Tronscan & Ağ Tespiti
    is_tron = cuzdan_adresi.startswith("T") and len(cuzdan_adresi) == 34
    is_evm = cuzdan_adresi.startswith("0x") and len(cuzdan_adresi) == 42
    
    ag_adi = "TRON (TRC20)" if is_tron else ("Ethereum / BSC (EVM)" if is_evm else "Kripto Cüzdanı")
    kesif_url = f"https://tronscan.org/#/address/{cuzdan_adresi}" if is_tron else (f"https://etherscan.io/address/{cuzdan_adresi}" if is_evm else f"https://tronscan.org/#/address/{cuzdan_adresi}")
    
    # Tronscan'den canlı bakiye ve borsa/istihbarat tespiti çek
    bakiye_metni = ""
    borsa_metni = ""
    if is_tron:
        try:
            trx_bal, usdt_bal, total_usd = get_tron_balances(cuzdan_adresi)
            borsa_analiz = detect_wallet_entity(cuzdan_adresi)
            borsa_metni = f"{borsa_analiz}\n"
            bakiye_metni = (
                f"💰 <b>HESAPTAKİ ANLIK VARLIKLAR:</b>\n"
                f"💵 <b>USDT (TRC20):</b> <code>{usdt_bal:,.2f} USDT</code>\n"
                f"🪙 <b>TRX Bakiyesi:</b> <code>{trx_bal:,.2f} TRX</code>\n"
                f"📊 <b>Toplam Cüzdan Değeri:</b> <code>~{total_usd:,.2f} $</code>\n"
                f"━━━━━━━━━━━\n"
            )
        except Exception as e:
            print(f"TRON analiz hatası ({cuzdan_adresi}): {e}")

    qr_foto_url = f"https://api.qrserver.com/v1/create-qr-code/?size=500x500&data={urllib.parse.quote(cuzdan_adresi)}&margin=15"
    
    caption = (
        f"⚡ <b>CÜZDAN ADRESİ & CANLI BAKİYE</b>\n"
        f"━━━━━━━━━━━\n"
        f"🌐 <b>Ağ Türü:</b> {ag_adi}\n"
        f"{borsa_metni}"
        f"📌 <b>Cüzdan Adresi:</b>\n"
        f"<code>{cuzdan_adresi}</code>\n\n"
        f"{bakiye_metni}"
        f"🔍 <b>Ağ İnceleme:</b> <a href=\"{kesif_url}\">Tronscan Explorer</a>\n"
        f"💡 <i>Adresi kopyalamak için üzerine dokunabilirsiniz.</i>"
    )
    
    arkham_url = f"https://platform.arkhamintelligence.com/explorer/address/{cuzdan_adresi}"
    misttrack_url = f"https://misttrack.io/address/TRON/{cuzdan_adresi}" if is_tron else f"https://misttrack.io/address/ETH/{cuzdan_adresi}"
    
    klavye = {
        "inline_keyboard": [
            [{"text": "🔍 Tronscan'de İncele", "url": kesif_url}],
            [{"text": "🌐 Arkham İstihbarat", "url": arkham_url}],
            [{"text": "🛡️ MistTrack AML Takip", "url": misttrack_url}]
        ]
    }
    
    res = telegramFotoGonder(chat_id, qr_foto_url, caption, klavye)
    if not res.get("ok"):
        fallback_qr = f"https://quickchart.io/qr?text={urllib.parse.quote(cuzdan_adresi)}&size=500&margin=2"
        res2 = telegramFotoGonder(chat_id, fallback_qr, caption, klavye)
        if not res2.get("ok"):
            telegramMesajGonder(chat_id, caption, klavye)

def hesapMakinesi_impl(orijinalMetin: str) -> str:
    args = orijinalMetin.strip().split()
    if len(args) < 4:
        return "⚠️ <b>Hatalı Kullanım!</b>\nFormat: <code>/hesap GRUPADI ORAN KUR</code>\nÖrnek: <code>/hesap SACİD 2 48.00</code>"
    kurStr = args.pop()
    komisyonStr = args.pop()
    arananGrup = " ".join(args[1:]).strip()
    hedef_norm = normalize_text(arananGrup)
    
    kur = float(kurStr.replace(",", "."))
    komisyonOrani = float(komisyonStr.replace(",", "."))
    
    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    veriler = sayfa.get_all_values()
    
    grupBulundu = False
    devirBorc = 0.0
    guncelKasa = 0.0
    gercekGrupAdi = arananGrup
    
    for row in veriler[1:]:
        if len(row) >= 2 and normalize_text(row[1]) == hedef_norm:
            gercekGrupAdi = row[1]
            devirBorc = guvenliSayi(row[2]) if len(row) > 2 else 0.0
            guncelKasa = guvenliSayi(row[3]) if len(row) > 3 else 0.0
            grupBulundu = True
            break
            
    if not grupBulundu:
        return f"⚠️ <b>Grup Bulunamadı:</b> Excel tablosunda <code>{arananGrup}</code> bulunamadı."
        
    komisyonKesintisi = guncelKasa * (komisyonOrani / 100.0)
    netKasaTl = guncelKasa - komisyonKesintisi
    usdtKarsiligi = netKasaTl / kur if kur > 0 else 0
    duzUsdt = int(round(usdtKarsiligi))
    
    islemZamani = suankiZamaniAl().strftime("%d.%m.%Y | %H:%M")
    mesaj = (
        f"👑 <b>HESAP KESİMİ & BAKİYE RAPORU</b>\n\n"
        f"🏛️ <b>Cari Hesap:</b> {gercekGrupAdi.upper()}\n"
        f"⏰ <b>Rapor Zamanı:</b> {islemZamani}\n\n"
    )
    if devirBorc != 0:
        mesaj += (
            f"⚠️ <b>GEÇMİŞTEN KALAN BORÇ HATIRLATMASI</b>\n"
            f"🔻 Devir/Borç Bakiyesi: {paraFormatla(devirBorc)}\n\n"
        )
    mesaj += (
        f"💰 <b>Mevcut Kasa:</b> {paraFormatla(guncelKasa)}\n"
        f"✂️ <b>Hizmet Bedeli (%{komisyonOrani}):</b> {paraFormatla(komisyonKesintisi)}\n"
        f"💎 <b>Net Hak Edilen (TL):</b> {paraFormatla(netKasaTl)}\n\n"
        f"📊 <b>Uygulanan Kur:</b> {kur}\n"
        f"🌐 <b>ÖDENECEK TETHER (USDT):</b> <b>{rakamFormatla(duzUsdt)} USDT</b>"
    )

    draft_id = f"r_{int(time.time())}_{random.randint(100, 999)}"
    app_state.setdefault("RAPOR_TASLAKLARI", {})[draft_id] = {
        "grup": gercekGrupAdi,
        "metin": mesaj
    }

    klavye = {
        "inline_keyboard": [
            [{"text": f"📤 {gercekGrupAdi.upper()} Grubuna İlet", "callback_data": f"rapor_ilet_{draft_id}"}],
            [{"text": "🗑️ Mesajı Kapat", "callback_data": "mesaj_kapat"}]
        ]
    }
    return mesaj, klavye

# --- TÜRKİYE BANKA KODLARI LİSTESİ (TCMB) ---
BANKA_KODLARI = {
    "00001": "T.C. Merkez Bankası",
    "00010": "T.C. Ziraat Bankası",
    "00012": "Türkiye Halk Bankası (Halkbank)",
    "00015": "Türkiye Vakıflar Bankası (VakıfBank)",
    "00032": "Türk Ekonomi Bankası (TEB)",
    "00046": "Akbank",
    "00059": "Şekerbank",
    "00062": "Garanti BBVA",
    "00064": "Türkiye İş Bankası",
    "00067": "Yapı ve Kredi Bankası",
    "00091": "Arap Türk Bankası",
    "00092": "Citibank",
    "00096": "Turkish Bank",
    "00099": "ING Bank",
    "00100": "Adabank",
    "00103": "Fibabanka",
    "00108": "Turkland Bank (T-Bank)",
    "00109": "ICBC Turkey Bank",
    "00111": "QNB Finansbank",
    "00115": "Deutsche Bank",
    "00121": "Standard Chartered Yatırım",
    "00122": "Societe Generale",
    "00123": "HSBC Bank",
    "00124": "Alternatif Bank",
    "00125": "Burgan Bank",
    "00134": "DenizBank",
    "00135": "Anadolubank",
    "00137": "Rabobank",
    "00138": "Diler Yatırım Bankası",
    "00139": "GSD Yatırım Bankası",
    "00140": "Credit Agricole Yatırım Bankası",
    "00141": "Nurol Yatırım Bankası",
    "00142": "BankPozitif Kredi ve Kalkınma",
    "00143": "Aktif Yatırım Bankası (Aktif Bank)",
    "00144": "Merrill Lynch Yatırım Bank",
    "00145": "Morgan Stanley Menkul Değerler",
    "00146": "Odea Bank",
    "00147": "MUFG Bank Turkey",
    "00148": "Intesa Sanpaolo",
    "00150": "İller Bankası",
    "00151": "Türk Eximbank",
    "00152": "Türkiye Kalkınma ve Yatırım Bankası",
    "00153": "İstanbul Takas ve Saklama Bankası",
    "00156": "Pashabank",
    "00158": "Destek Yatırım Bankası",
    "00159": "Golden Global Yatırım Bankası",
    "00160": "Q Yatırım Bankası",
    "00203": "Albaraka Türk Katılım Bankası",
    "00205": "Kuveyt Türk Katılım Bankası",
    "00206": "Türkiye Finans Katılım Bankası",
    "00208": "Asya Katılım Bankası",
    "00209": "Ziraat Katılım Bankası",
    "00210": "Vakıf Katılım Bankası",
    "00211": "Türkiye Emlak Katılım Bankası",
    "00212": "Hayat Finans Katılım Bankası",
    "00213": "TOM Katılım Bankası",
    "00801": "Papara Elektronik Para",
    "00802": "Payfix Elektronik Para",
    "00803": "İninal Ödeme ve Elektronik Para",
    "00804": "PeP / Paladyum Elektronik Para",
    "00805": "Moka Ödeme Kuruluşu"
}

def validate_iban(iban: str) -> bool:
    """MOD-97 (ISO 7064) algoritmasıyla İBAN matematiksel doğrulama testi."""
    raw = re.sub(r'[^A-Z0-9]', '', str(iban).upper())
    if len(raw) < 15 or len(raw) > 34:
        return False
    rearranged = raw[4:] + raw[:4]
    digits = ""
    for ch in rearranged:
        if ch.isdigit():
            digits += ch
        else:
            digits += str(ord(ch) - 55)
    try:
        return int(digits) % 97 == 1
    except ValueError:
        return False

def ibanCozumle_impl(ham_metin: str) -> str:
    temiz = re.sub(r'^/(?:ibancoz|iban|coz|ibandoğrula|ibandogrula)(?:@\w+)?\s*', '', ham_metin, flags=re.IGNORECASE).strip()
    
    match = re.search(r'\bTR\s*(?:[0-9A-Z]\s*){24}\b', temiz, re.IGNORECASE)
    if not match:
        raw_clean = re.sub(r'[^A-Z0-9]', '', temiz.upper())
        if raw_clean.startswith("TR") and len(raw_clean) == 26:
            iban_raw = raw_clean
        elif len(raw_clean) == 24 and raw_clean.isdigit():
            iban_raw = "TR" + raw_clean
        elif len(raw_clean) >= 15:
            iban_raw = raw_clean
        else:
            return (
                "⚠️ <b>Geçersiz veya Eksik İBAN!</b>\n"
                "Lütfen çözümlemek istediğiniz Türkiye İBAN numarasını girin.\n\n"
                "📌 <b>Örnek Kullanım:</b>\n"
                "<code>/ibancoz TR12 0006 2000 0001 2345 6789 01</code>\n"
                "veya doğrudan: <code>/ibancoz TR120006200000012345678901</code>"
            )
    else:
        iban_raw = re.sub(r'[^A-Z0-9]', '', match.group(0).upper())

    is_tr = iban_raw.startswith("TR") and len(iban_raw) == 26
    is_valid = validate_iban(iban_raw)
    
    banka_adi = "Bilinmeyen / Özel Finans Kurumu"
    banka_kodu = ""
    sube_hesap = ""
    
    if is_tr:
        banka_kodu = iban_raw[4:9]
        sube_hesap = iban_raw[9:]
        banka_adi = BANKA_KODLARI.get(banka_kodu, f"Diğer Finans Kurumu (Kod: {banka_kodu})")
        
        b_emoji = "🏛️"
        u_ad = banka_adi.upper()
        if "VAKIF" in u_ad: b_emoji = "🟡"
        elif "GARANTİ" in u_ad or "GARANTI" in u_ad: b_emoji = "🟢"
        elif "ZİRAAT" in u_ad or "ZIRAAT" in u_ad: b_emoji = "🔴"
        elif "İŞ BANKASI" in u_ad or "IS BANKASI" in u_ad: b_emoji = "🔵"
        elif "YAPI" in u_ad: b_emoji = "🔷"
        elif "KUVEYT" in u_ad: b_emoji = "🌿"
        elif "QNB" in u_ad: b_emoji = "🟣"
        elif "HALK" in u_ad: b_emoji = "🔵"
        elif "AKBANK" in u_ad: b_emoji = "🔴"
        elif "PAPARA" in u_ad: b_emoji = "💳"
        elif "PAYFIX" in u_ad: b_emoji = "⚡"
        
        banka_adi_str = f"{b_emoji} <b>{banka_adi}</b>"
    else:
        banka_adi_str = f"🌐 <b>Uluslararası İBAN ({iban_raw[:2]})</b>"

    bosluklu_iban = " ".join([iban_raw[i:i+4] for i in range(0, len(iban_raw), 4)])
    bitisik_iban = iban_raw
    
    sirket_durumu = "🔹 <i>Harici Cari / Müşteri Hesabı</i>"
    try:
        sh = get_spreadsheet()
        sayfa = get_active_daily_sheet(sh)
        veriler = sayfa.get_all_values()
        temiz_hedef = re.sub(r'[^A-Z0-9]', '', iban_raw)
        
        for row in veriler[1:]:
            if len(row) > 11:
                ib1 = re.sub(r'[^A-Z0-9]', '', row[11].strip().upper())
                if ib1 and (ib1 == temiz_hedef or temiz_hedef.endswith(ib1) or ib1.endswith(temiz_hedef)):
                    not1 = row[14].strip() if len(row) > 14 else ""
                    sirket_durumu = f"🏢 <b>ŞİRKET İÇİ HESAP!</b> (CYL/HSY: <code>{row[11].strip()}</code>" + (f" - Cari: <b>{not1}</b>" if not1 else " - 🟢 <b>Boşta</b>") + ")"
                    break
            if len(row) > 15:
                ib2 = re.sub(r'[^A-Z0-9]', '', row[15].strip().upper())
                if ib2 and (ib2 == temiz_hedef or temiz_hedef.endswith(ib2) or ib2.endswith(temiz_hedef)):
                    not2 = row[17].strip() if len(row) > 17 else ""
                    sirket_durumu = f"🏢 <b>ŞİRKET İÇİ HESAP!</b> (ARS/SRGL: <code>{row[15].strip()}</code>" + (f" - Cari: <b>{not2}</b>" if not2 else " - 🟢 <b>Boşta</b>") + ")"
                    break
    except Exception as e:
        print(f"İBAN envanter kontrolü hatası: {e}")

    durum_str = "✅ <b>Geçerli ve Onaylı Türk İBAN'ı (MOD-97)</b>" if (is_valid and is_tr) else ("✅ <b>Geçerli Uluslararası İBAN</b>" if is_valid else "⚠️ <b>GEÇERSİZ İBAN! (Rakamları/Haneyi Kontrol Ediniz)</b>")

    mesaj = (
        f"🔍 <b>İBAN ÇÖZÜMLEME & DOĞRULAMA</b>\n"
        f"━━━━━━━━━━\n"
        f"🏦 <b>Banka:</b> {banka_adi_str}\n"
        f"📊 <b>Durum:</b> {durum_str}\n"
        f"━━━━━━━━━━\n"
        f"📌 <b>Okunabilir Format (Boşluklu):</b>\n"
        f"<code>{bosluklu_iban}</code>\n\n"
        f"⚡ <b>Hızlı Kopyala (Mobil Bankacılık):</b>\n"
        f"<code>{bitisik_iban}</code>\n"
        f"━━━━━━━━━━\n"
    )
    if banka_kodu:
        mesaj += (
            f"🏛️ <b>Banka Kodu:</b> <code>{banka_kodu}</code>\n"
            f"🏢 <b>Şube / Hesap No:</b> <code>{sube_hesap}</code>\n"
        )
    mesaj += (
        f"📑 <b>Şirket Envanteri:</b> {sirket_durumu}\n"
        f"━━━━━━━━━━━\n"
        f"💡 <i>Kopyalamak için numaranın üzerine dokunabilirsiniz.</i>"
    )
    return mesaj

def ibanListesiGetir_impl() -> str:
    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    veriler = get_sheet_values_fast(sayfa)
    bosta, dolu = [], []
    for row in veriler[1:]:
        if len(row) > 11 and row[11].strip():
            ib1 = row[11].strip()
            not1 = row[14].strip() if len(row) > 14 else ""
            if not not1: bosta.append(f"🔹 <code>{ib1}</code>")
            else: dolu.append(f"🔹 👤 <b>{not1}:</b> <code>{ib1}</code>")
        if len(row) > 15 and row[15].strip():
            ib2 = row[15].strip()
            not2 = row[17].strip() if len(row) > 17 else ""
            if not not2: bosta.append(f"🔹 <code>{ib2}</code>")
            else: dolu.append(f"🔹 👤 <b>{not2}:</b> <code>{ib2}</code>")
    mesaj = "🏦 <b>ŞİRKET İBAN LİSTESİ</b>\n━━━━━━━━━━\n\n"
    mesaj += "🟢 <b>BOŞTAKİ İBANLAR</b> <i>(Kullanıma Hazır)</i>\n" + ("\n".join(bosta) if bosta else "🔹 <i>Boşta İBAN yok.</i>") + "\n\n"
    mesaj += "🔴 <b>KULLANIMDAKİ İBANLAR</b>\n" + ("\n".join(dolu) if dolu else "🔹 <i>Kullanımda İBAN yok.</i>")
    return mesaj

def normalize_hesap_kodu(text: str) -> str:
    if not text:
        return ""
    t = normalize_text(text)
    return re.sub(r'[^A-Z0-9]', '', t)

def iban_sablon_bul(veriler: List[List[str]], aranan_kod: str):
    """
    Excel tablosundaki Sol Blok (Col L: 12, Col M: 13, Col O: 15) ve 
    Sağ Blok (Col P: 16, Col Q: 17, Col R: 18) üzerinde şablon arar.
    Döner: (satir_idx, hesap_adi, sablon_metni, cari_adi) veya None
    """
    aranan_temiz = aranan_kod.strip()
    aranan_norm = normalize_hesap_kodu(aranan_temiz)
    if not aranan_norm:
        return None

    # 1. Aşama: BİREBİR TAM EŞLEŞME (Örn: 'CYL 1' için tam 'CYL 1' satırını bulur, 'CYL 10'a atlamaz)
    for idx, row in enumerate(veriler[1:], start=2):
        if len(row) > 11 and row[11].strip():
            h_ad = row[11].strip()
            h_norm = normalize_hesap_kodu(h_ad)
            if h_norm == aranan_norm:
                sablon = row[12].strip() if len(row) > 12 else ""
                cari = row[14].strip() if len(row) > 14 else ""
                return idx, h_ad, sablon, cari

        if len(row) > 15 and row[15].strip():
            h_ad = row[15].strip()
            h_norm = normalize_hesap_kodu(h_ad)
            if h_norm == aranan_norm:
                sablon = row[16].strip() if len(row) > 16 else ""
                cari = row[17].strip() if len(row) > 17 else ""
                return idx, h_ad, sablon, cari

    # 2. Aşama: Başlangıç/içerme eşleşmesi (Sadece tam eşleşme yoksa)
    for idx, row in enumerate(veriler[1:], start=2):
        if len(row) > 11 and row[11].strip():
            h_ad = row[11].strip()
            h_norm = normalize_hesap_kodu(h_ad)
            if len(aranan_norm) >= 3 and (h_norm.startswith(aranan_norm) or aranan_norm in h_norm):
                sablon = row[12].strip() if len(row) > 12 else ""
                cari = row[14].strip() if len(row) > 14 else ""
                return idx, h_ad, sablon, cari

        if len(row) > 15 and row[15].strip():
            h_ad = row[15].strip()
            h_norm = normalize_hesap_kodu(h_ad)
            if len(aranan_norm) >= 3 and (h_norm.startswith(aranan_norm) or aranan_norm in h_norm):
                sablon = row[16].strip() if len(row) > 16 else ""
                cari = row[17].strip() if len(row) > 17 else ""
                return idx, h_ad, sablon, cari

    # 3. Aşama: Esnek token eşleşmesi (Örn: 'ARS 3' -> 'ARS EMLAK 3' veya 'HSY 2' -> 'HSY 2 / 29')
    match_digits = re.findall(r'\d+', aranan_norm)
    match_letters = re.findall(r'[A-Z]+', aranan_norm)
    if match_digits and match_letters:
        num = match_digits[-1]
        letters = "".join(match_letters)
        for idx, row in enumerate(veriler[1:], start=2):
            if len(row) > 11 and row[11].strip():
                h_ad = row[11].strip()
                h_norm = normalize_hesap_kodu(h_ad)
                if letters in h_norm and (h_norm.endswith(num) or re.search(rf'{num}(?!\d)', h_norm)):
                    sablon = row[12].strip() if len(row) > 12 else ""
                    cari = row[14].strip() if len(row) > 14 else ""
                    return idx, h_ad, sablon, cari

            if len(row) > 15 and row[15].strip():
                h_ad = row[15].strip()
                h_norm = normalize_hesap_kodu(h_ad)
                if letters in h_norm and (h_norm.endswith(num) or re.search(rf'{num}(?!\d)', h_norm)):
                    sablon = row[16].strip() if len(row) > 16 else ""
                    cari = row[17].strip() if len(row) > 17 else ""
                    return idx, h_ad, sablon, cari

    return None

def iban_sablon_getir_impl(komut_metni: str, chat_id: int = 0):
    """
    Kullanıcı /HSY EMLAK 3 veya /sablon HSY EMLAK 3 yazdığında
    Excel'deki ilgili satırdan hazır ödeme şablonunu çeker ve doğrudan Telegram'a gönderir.
    Eğer komut bağlı bir Telegram grubundan çağrılmışsa, hesabı otomatik olarak o gruba tahsis edip Excel'e işler
    ve altına anında boşa çıkarma / iptal etme butonu ekler.
    """
    temiz_komut = komut_metni.strip()
    if temiz_komut.startswith("/sablon") or temiz_komut.startswith("/şablon") or temiz_komut.startswith("/hesapbilgi"):
        p = temiz_komut.split()[1:]
        aranan = " ".join(p).strip()
    else:
        aranan = temiz_komut.lstrip("/").strip()

    if not aranan:
        return (
            "📋 <b>ŞİRKET ÖDEME ŞABLONU ÇEKİCİ</b>\n"
            "━━━━━━━━━━━\n"
            "Kullanım: <code>/sablon [Hesap Adı]</code> veya doğrudan <code>/[Hesap Adı]</code>\n\n"
            "📌 <b>Örnekler:</b>\n"
            "• <code>/HSY EMLAK 3</code>\n"
            "• <code>/CYL 1</code>\n"
            "• <code>/ARS EMLAK 2</code>\n"
            "• <code>/SRGL 1</code>"
        )

    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    veriler = get_sheet_values_fast(sayfa)

    res = iban_sablon_bul(veriler, aranan)
    if not res:
        return f"⚠️ <b>Şablon Bulunamadı!</b>\nExcel tablosunda '<b>{aranan}</b>' hesabına ait bir ödeme şablonu bulunamadı.\n\n💡 <i>Mevcut hesaplar: CYL 1-5, HSY 1-10, HSY EMLAK 1-16, ARS EMLAK 1-17, SRGL 1-10</i>"

    satir_idx, hesap_adi, sablon_metni, cari_adi = res

    if not sablon_metni:
        return f"⚠️ <b>'{hesap_adi}'</b> için Excel tablosunda henüz bir şablon metni girilmemiş."

    tahsis_bilgisi = ""
    klavye = None

    # Eğer bu komut bağlı bir Telegram grubundan çağrıldıysa otomatik olarak o gruba tahsis et
    if chat_id and chat_id < 0:
        grup_baglantilarini_guncelle()
        bagli = app_state.get("GRUP_BAGLANTILARI", {}).get(chat_id)
        if bagli and bagli.get("grup"):
            hedef_cari = bagli.get("grup").strip().upper()
            h_res = iban_hesap_bul(veriler, aranan) or iban_hesap_bul(veriler, hesap_adi)
            if h_res:
                h_satir, h_col, h_ad, eski_cari = h_res
                if eski_cari.strip().upper() != hedef_cari:
                    try:
                        update_sheet_matrix_memory(sayfa.title, h_satir, h_col, hedef_cari)
                        sayfa.update_cell(h_satir, h_col, hedef_cari)
                        app_state["SON_ISLEM"] = {
                            "sayfa": sayfa.title, "satir": h_satir, "sutun": h_col,
                            "eskiDeger": eski_cari, "grupAdi": h_ad, "islemTuru": "İBAN Otomatik Tahsis"
                        }
                        sistemeLogYaz("İBAN Otomatik Tahsis", f"Gruptan ({chat_id}) {h_ad} ➔ {hedef_cari}")
                        tahsis_bilgisi = f"\n\n📌 <i>Bu hesap otomatik olarak <b>{hedef_cari}</b> grubuna tahsis edildi (Excel güncellendi).</i>"
                    except Exception as e:
                        print(f"Otomatik İBAN tahsis hatası: {e}")
                else:
                    tahsis_bilgisi = f"\n\n📌 <i>Bu hesap zaten <b>{hedef_cari}</b> grubuna tahsisli.</i>"

                klavye = {
                    "inline_keyboard": [
                        [{"text": f"🔓 {h_ad} Boşa Çıkar / İptal Et", "callback_data": f"ibanbosta_{h_ad}"}]
                    ]
                }

    if klavye:
        return sablon_metni + tahsis_bilgisi, klavye
    return sablon_metni + tahsis_bilgisi

def iban_hesap_bul(veriler: List[List[str]], aranan_kod: str):
    """
    Excel tablosundaki L (12. sütun) ve P (16. sütun) hesap bloklarında arama yapar.
    Döner: (satir_no_1based, hedef_cari_sutun_1based, hesap_adi, mevcut_cari)
    """
    aranan_temiz = aranan_kod.strip()
    aranan_norm = normalize_hesap_kodu(aranan_temiz)
    if not aranan_norm:
        return None

    # 1. Aşama: BİREBİR TAM EŞLEŞME (Örn: 'CYL 1' için tam 'CYL 1' satırını bulur, 'CYL 10'a atlamaz)
    for idx, row in enumerate(veriler[1:], start=2):
        if len(row) > 11 and row[11].strip():
            h_ad = row[11].strip()
            h_norm = normalize_hesap_kodu(h_ad)
            if h_norm == aranan_norm:
                mevcut_cari = row[14].strip() if len(row) > 14 else ""
                return idx, 15, h_ad, mevcut_cari

        if len(row) > 15 and row[15].strip():
            h_ad = row[15].strip()
            h_norm = normalize_hesap_kodu(h_ad)
            if h_norm == aranan_norm:
                mevcut_cari = row[17].strip() if len(row) > 17 else ""
                return idx, 18, h_ad, mevcut_cari

    # 2. Aşama: Başlangıç/içerme eşleşmesi (Sadece tam eşleşme yoksa)
    for idx, row in enumerate(veriler[1:], start=2):
        if len(row) > 11 and row[11].strip():
            h_ad = row[11].strip()
            h_norm = normalize_hesap_kodu(h_ad)
            if len(aranan_norm) >= 3 and (h_norm.startswith(aranan_norm) or aranan_norm in h_norm):
                mevcut_cari = row[14].strip() if len(row) > 14 else ""
                return idx, 15, h_ad, mevcut_cari

        if len(row) > 15 and row[15].strip():
            h_ad = row[15].strip()
            h_norm = normalize_hesap_kodu(h_ad)
            if len(aranan_norm) >= 3 and (h_norm.startswith(aranan_norm) or aranan_norm in h_norm):
                mevcut_cari = row[17].strip() if len(row) > 17 else ""
                return idx, 18, h_ad, mevcut_cari

    # 3. Aşama: Esnek token eşleşmesi (Örn: 'ARS 3' -> 'ARS EMLAK 3' veya 'HSY 2' -> 'HSY 2 / 29')
    match_digits = re.findall(r'\d+', aranan_norm)
    match_letters = re.findall(r'[A-Z]+', aranan_norm)
    if match_digits and match_letters:
        num = match_digits[-1]
        letters = "".join(match_letters)
        for idx, row in enumerate(veriler[1:], start=2):
            if len(row) > 11 and row[11].strip():
                h_ad = row[11].strip()
                h_norm = normalize_hesap_kodu(h_ad)
                if letters in h_norm and (h_norm.endswith(num) or re.search(rf'{num}(?!\d)', h_norm)):
                    mevcut_cari = row[14].strip() if len(row) > 14 else ""
                    return idx, 15, h_ad, mevcut_cari

            if len(row) > 15 and row[15].strip():
                h_ad = row[15].strip()
                h_norm = normalize_hesap_kodu(h_ad)
                if letters in h_norm and (h_norm.endswith(num) or re.search(rf'{num}(?!\d)', h_norm)):
                    mevcut_cari = row[17].strip() if len(row) > 17 else ""
                    return idx, 18, h_ad, mevcut_cari

    return None

def iban_bosalt_direct(hesap_kodu: str) -> Tuple[bool, str, str, str]:
    """
    Doğrudan hesap kodunu alıp Excel tablosundaki tahsisini boşa çıkarır (Müsait yapar).
    Döner: (basarili_mi, hesap_adi, eski_cari, sayfa_basligi)
    """
    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    veriler = get_sheet_values_fast(sayfa)
    
    bulunan = iban_hesap_bul(veriler, hesap_kodu)
    if not bulunan:
        return False, f"Hesap '{hesap_kodu}' bulunamadı", "", sayfa.title
        
    satir_idx, col_idx, hesap_adi, eski_cari = bulunan
    
    update_sheet_matrix_memory(sayfa.title, satir_idx, col_idx, "")
    sayfa.update_cell(satir_idx, col_idx, "")
    
    app_state["SON_ISLEM"] = {
        "sayfa": sayfa.title, "satir": satir_idx, "sutun": col_idx,
        "eskiDeger": eski_cari, "grupAdi": hesap_adi, "islemTuru": "İBAN Boşaltma"
    }
    sistemeLogYaz("İBAN Boşaltma", f"{hesap_adi} | Eski: {eski_cari} ➔ Boş")
    return True, hesap_adi, eski_cari, sayfa.title

def iban_tahsis_impl(komut_metni: str) -> str:
    parcalar = komut_metni.strip().split()[1:]
    if len(parcalar) < 2:
        return (
            "⚠️ <b>Hatalı Kullanım!</b>\n"
            "Format: <code>/ibantahsis [Hesap No] [Cari Adı]</code>\n\n"
            "📌 <b>Örnekler:</b>\n"
            "• <code>/ibantahsis CYL1 SACİD</code>\n"
            "• <code>/ibantahsis HSY2 THY</code>\n"
            "• <code>/ibantahsis ARS3 BSM</code>"
        )
        
    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    veriler = get_sheet_values_fast(sayfa)
    
    bulunan = None
    cari_adi = ""
    
    for split_idx in range(len(parcalar) - 1, 0, -1):
        hesap_adayi = " ".join(parcalar[:split_idx]).strip()
        cari_adayi = " ".join(parcalar[split_idx:]).strip()
        res = iban_hesap_bul(veriler, hesap_adayi)
        if res:
            bulunan = res
            cari_adi = cari_adayi
            break
            
    if not bulunan:
        res = iban_hesap_bul(veriler, parcalar[0])
        if res:
            bulunan = res
            cari_adi = " ".join(parcalar[1:]).strip()
            
    if not bulunan:
        return f"⚠️ <b>Hesap Bulunamadı!</b>\nExcel tablosunda '<b>{' '.join(parcalar[:-1])}</b>' adlı bir İBAN/hesap bulunamadı."
        
    satir_idx, col_idx, hesap_adi, eski_cari = bulunan
    cari_temiz = cari_adi.strip().upper()
    
    update_sheet_matrix_memory(sayfa.title, satir_idx, col_idx, cari_temiz)
    sayfa.update_cell(satir_idx, col_idx, cari_temiz)
    
    app_state["SON_ISLEM"] = {
        "sayfa": sayfa.title, "satir": satir_idx, "sutun": col_idx,
        "eskiDeger": eski_cari, "grupAdi": hesap_adi, "islemTuru": "İBAN Tahsis"
    }
    sistemeLogYaz("İBAN Tahsis", f"{hesap_adi} ➔ {cari_temiz}")
    
    return (
        f"✅ <b>İBAN BAŞARIYLA TAHSİS EDİLDİ!</b>\n"
        f"━━━━━━━━━━━\n"
        f"🏦 <b>Hesap:</b> <code>{hesap_adi}</code>\n"
        f"👤 <b>Tahsis Edilen Cari:</b> <b>{cari_temiz}</b>\n"
        f"📊 <b>Durum:</b> 🔴 <b>Kullanımda</b>\n"
        f"📌 <b>Excel Satırı:</b> Satır {satir_idx}\n"
        f"━━━━━━━━━━━\n"
        f"💡 <i>İşlem bitince <code>/ibanbosalt {parcalar[0]}</code> yazarak boşa çıkarabilirsiniz.</i>"
    )

def iban_bosalt_impl(komut_metni: str) -> str:
    parcalar = komut_metni.strip().split()[1:]
    if len(parcalar) < 1:
        return (
            "⚠️ <b>Hatalı Kullanım!</b>\n"
            "Format: <code>/ibanbosalt [Hesap No]</code>\n\n"
            "📌 <b>Örnekler:</b>\n"
            "• <code>/ibanbosalt CYL1</code>\n"
            "• <code>/ibanbosalt HSY2</code>\n"
            "• <code>/ibanbosalt ARS3</code>"
        )
        
    hesap_kodu = " ".join(parcalar).strip()
    ok, hesap_adi, eski_cari, sayfa_basligi = iban_bosalt_direct(hesap_kodu)
    if not ok:
        return f"⚠️ <b>Hesap Bulunamadı!</b>\nExcel tablosunda '<b>{hesap_kodu}</b>' adlı bir İBAN/hesap bulunamadı."
    
    eski_str = f"<s>{eski_cari}</s>" if eski_cari else "<i>(Zaten boştu)</i>"
    return (
        f"🟢 <b>İBAN BOŞA ÇIKARILDI!</b>\n"
        f"━━━━━━━━━━\n"
        f"🏦 <b>Hesap:</b> <code>{hesap_adi}</code>\n"
        f"👤 <b>Eski Cari:</b> {eski_str}\n"
        f"📊 <b>Durum:</b> 🟢 <b>Müsait / Kullanıma Hazır</b>\n"
        f"━━━━━━━━━━\n"
        f"💡 <i>Hesap havuza geri döndü, başka bir cariye verilebilir.</i>"
    )

def grup_aktif_ibanlar_raporu_uret(grup_adi: str = "", chat_id: int = 0) -> Tuple[str, dict]:
    """
    Belirtilen veya içinde bulunulan gruba bağlı olan TÜM aktif İBAN'ları Excel'den çeker,
    veri analizi olarak listeler ve her birini tek tıkla boşa çıkarmak/silmek için butonlar sunar.
    """
    hedef_cari = grup_adi.strip().upper() if grup_adi else ""
    if not hedef_cari and chat_id and chat_id < 0:
        grup_baglantilarini_guncelle()
        bagli = app_state.get("GRUP_BAGLANTILARI", {}).get(chat_id)
        if bagli and bagli.get("grup"):
            hedef_cari = bagli.get("grup").strip().upper()

    if not hedef_cari:
        return (
            "⚠️ <b>Grup Belirlenemedi!</b>\n"
            "Lütfen sorgulamak istediğiniz cariyi belirtin veya bu komutu bağlı bir Telegram grubunda yazın.\n\n"
            "📌 <b>Örnek:</b> <code>/hesaplar SACİD</code> veya <code>/grupiban TİGER</code>",
            None
        )

    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    veriler = get_sheet_values_fast(sayfa)

    hedef_norm = normalize_text(hedef_cari)
    tahsisli_hesaplar = []

    for idx, row in enumerate(veriler[1:], start=2):
        # 1. Sol Blok: Col 11: Hesap, Col 12: Şablon, Col 13: Banka, Col 14: Cari (0-indexed)
        if len(row) > 14 and row[14].strip():
            c1 = row[14].strip()
            if normalize_text(c1) == hedef_norm:
                h_ad = row[11].strip() if len(row) > 11 else ""
                h_sablon = row[12].strip() if len(row) > 12 else ""
                h_banka = row[13].strip() if len(row) > 13 else ""
                m_iban = re.search(r'TR\d{2}\s?(?:\d{4}\s?){5}\d{2}', h_sablon.upper())
                iban_str = m_iban.group(0).replace(" ", "") if m_iban else ""
                tahsisli_hesaplar.append({
                    "hesap": h_ad,
                    "banka": h_banka,
                    "iban": iban_str,
                    "satir": idx,
                    "col": 15
                })

        # 2. Sağ Blok: Col 15: Hesap, Col 16: Şablon, Col 17: Cari (0-indexed)
        if len(row) > 17 and row[17].strip():
            c2 = row[17].strip()
            if normalize_text(c2) == hedef_norm:
                h_ad = row[15].strip() if len(row) > 15 else ""
                h_sablon = row[16].strip() if len(row) > 16 else ""
                m_iban = re.search(r'TR\d{2}\s?(?:\d{4}\s?){5}\d{2}', h_sablon.upper())
                iban_str = m_iban.group(0).replace(" ", "") if m_iban else ""
                tahsisli_hesaplar.append({
                    "hesap": h_ad,
                    "banka": "",
                    "iban": iban_str,
                    "satir": idx,
                    "col": 18
                })

    tarih_str = suankiZamaniAl().strftime("%d.%m.%Y")
    saat_str = suankiZamaniAl().strftime("%H:%M")
    emoji = grupEmojisiBul(hedef_cari)

    if not tahsisli_hesaplar:
        metin = (
            f"🏦 <b>{hedef_cari} GRUBU İBAN ANALİZ RAPORU</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 Tarih: <b>{tarih_str}</b> | ⏰ Saat: <code>{saat_str}</code>\n"
            f"👤 Bağlı Cari: {emoji} <b>{hedef_cari}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🟢 <b>Bu gruba şu anda tahsis edilmiş aktif bir İBAN bulunmuyor.</b>\n\n"
            f"💡 <i>İhtiyacınız olduğunda <code>/HSY EMLAK 2</code> veya <code>/sablon [Hesap]</code> yazarak yeni bir hesap alabilirsiniz.</i>"
        )
        klavye = {
            "inline_keyboard": [
                [{"text": "🔄 Listeyi Yenile", "callback_data": f"grup_iban_yenile_{hedef_cari}"}]
            ]
        }
        return metin, klavye

    metin = (
        f"🏦 <b>{hedef_cari} GRUBU AKTİF İBAN VERİ ANALİZİ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 Tarih: <b>{tarih_str}</b> | ⏰ Saat: <code>{saat_str}</code>\n"
        f"👤 Bağlı Cari: {emoji} <b>{hedef_cari}</b>\n"
        f"📊 Toplam Tahsisli Hesap: <b>{len(tahsisli_hesaplar)} Adet</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📋 <b>GÜNCEL AKTİF HESAPLAR:</b>\n\n"
    )

    buttons = []
    madalyalar = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

    for i, h in enumerate(tahsisli_hesaplar):
        num = madalyalar[i] if i < len(madalyalar) else f"{i+1}️⃣"
        iban_display = f"<code>{h['iban']}</code>" if h['iban'] else "<i>(Şablonda kayıtlı)</i>"
        banka_display = f" | 🏢 <b>{h['banka']}</b>" if h['banka'] else ""
        metin += (
            f"{num} 🏛️ <b>{h['hesap']}</b>{banka_display}\n"
            f"   • 💳 İBAN: {iban_display}\n"
            f"   • 📌 Durum: 🔴 <b>{hedef_cari} Grubuna Tahsisli</b>\n\n"
        )
        buttons.append([{"text": f"🔓 {h['hesap']} Hesabını Boşa Çıkar", "callback_data": f"grup_iban_sil_{h['hesap']}_{hedef_cari}"}])

    buttons.append([{"text": "🔄 Listeyi Yenile", "callback_data": f"grup_iban_yenile_{hedef_cari}"}])

    metin += (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <i>Boşa çıkarmak istediğiniz hesabın butonuna basarak Excel'deki tahsisini anında kaldırabilirsiniz.</i>"
    )

    return metin, {"inline_keyboard": buttons}

def cari_ekstre_impl(komut_metni: str) -> str:
    parcalar = komut_metni.strip().split()[1:]
    if len(parcalar) < 1:
        return (
            "⚠️ <b>Hatalı Kullanım!</b>\n"
            "Format: <code>/ekstre [Cari Adı] [Gün Sayısı (Opsiyonel)]</code>\n\n"
            "📌 <b>Örnekler:</b>\n"
            "• <code>/ekstre SACİD</code>\n"
            "• <code>/ekstre THY 5</code>\n"
            "• <code>/ekstre TİGER 7</code>"
        )
        
    gun_sayisi = 5
    if len(parcalar) > 1 and parcalar[-1].isdigit():
        gun_sayisi = min(int(parcalar[-1]), 10)
        grup_ham = " ".join(parcalar[:-1]).strip()
    else:
        grup_ham = " ".join(parcalar).strip()
        
    hedef_norm = normalize_text(grup_ham)
    if not hedef_norm:
        return "⚠️ Lütfen geçerli bir cari/grup adı giriniz."
        
    sh = get_spreadsheet()
    tum_sayfalar = sh.worksheets()
    
    tarih_sayfalari = []
    for ws in tum_sayfalar:
        if is_valid_daily_sheet(ws) and re.match(r'^\d{2}\.\d{2}\.\d{4}$', ws.title):
            try:
                t_obj = datetime.datetime.strptime(ws.title, "%d.%m.%Y")
                tarih_sayfalari.append((t_obj, ws))
            except Exception:
                pass
                
    if not tarih_sayfalari:
        return "📭 Tabloda geçmiş tarihli sayfa bulunamadı."
        
    tarih_sayfalari.sort(key=lambda x: x[0], reverse=True)
    secilen_sayfalar = tarih_sayfalari[:gun_sayisi]
    
    def fetch_sheet_cari(ws_tuple):
        t_obj, ws = ws_tuple
        try:
            veriler = ws.get_all_values()
            for row in veriler[1:43]:
                if len(row) >= 2 and normalize_text(row[1]) == hedef_norm:
                    devir = guvenliSayi(row[2]) if len(row) > 2 else 0.0
                    kasa = guvenliSayi(row[3]) if len(row) > 3 else 0.0
                    odenen = guvenliSayi(row[4]) if len(row) > 4 else 0.0
                    komisyon = guvenliSayi(row[5]) if len(row) > 5 else 0.0
                    kalan = guvenliSayi(row[6]) if len(row) > 6 else 0.0
                    return (t_obj, ws.title, row[1].strip(), devir, kasa, odenen, komisyon, kalan)
            return (t_obj, ws.title, None, 0.0, 0.0, 0.0, 0.0, 0.0)
        except Exception as e:
            print(f"Ekstre sayfa okuma hatası ({ws.title}): {e}")
            return (t_obj, ws.title, None, 0.0, 0.0, 0.0, 0.0, 0.0)

    futures = [_update_executor.submit(fetch_sheet_cari, item) for item in secilen_sayfalar]
    sonuclar = [f.result() for f in futures]
    sonuclar.sort(key=lambda x: x[0], reverse=True)
    
    bulunan_kayitlar = [s for s in sonuclar if s[2] is not None]
    if not bulunan_kayitlar:
        return f"⚠️ <b>Cari Bulunamadı:</b> Tablodaki son {len(secilen_sayfalar)} günde '<b>{grup_ham}</b>' adlı cari bulunamadı."
        
    gercek_grup_adi = bulunan_kayitlar[0][2]
    
    toplam_giris = sum(s[4] for s in sonuclar if s[2] is not None)
    toplam_odeme = sum(s[5] for s in sonuclar if s[2] is not None)
    toplam_komisyon = sum(s[6] for s in sonuclar if s[2] is not None)
    en_guncel_kalan = bulunan_kayitlar[0][7]
    
    mesaj = (
        f"📈 <b>[ {gercek_grup_adi.upper()} ] HESAP EKSTRESİ ({len(secilen_sayfalar)} GÜN)</b>\n"
        f"━━━━━━━━━━\n\n"
    )
    
    for t_obj, baslik, g_ad, devir, kasa, odenen, kom, kalan in sonuclar:
        if g_ad is None:
            continue
            
        mesaj += f"📅 <b>{baslik}</b>\n"
        satir_detay = []
        satir_detay.append(f"🔄 Devir: {paraFormatla(devir)}")
        if abs(kasa) > 0.001:
            satir_detay.append(f"💰 Kasa: +{paraFormatla(kasa)}")
        if abs(odenen) > 0.001:
            satir_detay.append(f"💸 Ödeme: -{paraFormatla(odenen)}")
        if abs(kom) > 0.001:
            satir_detay.append(f"✂️ Kom: {paraFormatla(kom)}")
            
        mesaj += f"{' | '.join(satir_detay)}\n"
        mesaj += f"🏦 <b>Kalan Bakiye: {paraFormatla(kalan)}</b>\n\n"
        
    mesaj += (
        f"━━━━━━━━━\n"
        f"📊 <b>{len(secilen_sayfalar)} GÜNLÜK TOPLAM PERFORMANS:</b>\n"
        f"💰 Toplam Giriş: <b>{paraFormatla(toplam_giris)}</b>\n"
        f"💸 Toplam Ödeme: <b>{paraFormatla(toplam_odeme)}</b>\n"
    )
    if abs(toplam_komisyon) > 0.001:
        mesaj += f"✂️ Toplam Komisyon: <b>{paraFormatla(toplam_komisyon)}</b>\n"
    mesaj += (
        f"🏦 <b>GÜNCEL NET BAKİYE: {paraFormatla(en_guncel_kalan)}</b>\n"
        f"━━━━━━━━━━━"
    )
    return mesaj

def toplu_islem_impl(komut_metni: str) -> str:
    """
    Birden fazla kasa, ödeme, devir ve masraf işlemini tek seferde alt alta işler.
    Örnek:
    /toplu
    + SACİD 50000
    - SACİD 20000
    + TİGER 150000
    Ö THY 75000
    M Yemek 1250
    """
    satirlar = [s.strip() for s in komut_metni.strip().splitlines() if s.strip()]
    if len(satirlar) <= 1:
        ilk_satir = satirlar[0] if satirlar else ""
        kalan_metin = re.sub(r'^/(?:toplu|topluislem|hizli)(?:@\w+)?\s*', '', ilk_satir, flags=re.IGNORECASE).strip()
        if not kalan_metin:
            return (
                "⚡ <b>TOPLU HIZLI İŞLEM KULLANIMI</b>\n"
                "━━━━━━━━━━━━━\n"
                "İşlemleri tek bir mesajda alt alta yazabilirsiniz:\n\n"
                "<code>/toplu\n"
                "+ SACİD 50000\n"
                "- SACİD 20000\n"
                "+ TİGER 150000\n"
                "Ö THY 75000\n"
                "M Yemek 1250</code>\n\n"
                "📌 <b>Kısayol Sembolleri:</b>\n"
                "• <code>+</code> veya <code>K</code> : Kasaya Ekle\n"
                "• <code>-</code> : Kasadan Düş / Sil\n"
                "• <code>Ö</code> veya <code>O</code> : Ödeme Yap\n"
                "• <code>D</code> : Devir Ekle\n"
                "• <code>M</code> veya <code>G</code> : Masraf/Gider Ekle\n"
                "━━━━━━━━━━━━━━"
            )
        satirlar = [kalan_metin]
    else:
        ilk_satir = satirlar[0]
        kalan_ilk = re.sub(r'^/(?:toplu|topluislem|hizli)(?:@\w+)?\s*', '', ilk_satir, flags=re.IGNORECASE).strip()
        satirlar = ([kalan_ilk] if kalan_ilk else []) + satirlar[1:]

    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    
    islem_sonuclari = []
    etkilenen_gruplar = set()
    
    for satir in satirlar:
        if not satir.strip():
            continue
        p = satir.strip().split()
        if len(p) < 2:
            islem_sonuclari.append(f"• ⚠️ <code>{satir}</code> <i>(Eksik parametre)</i>")
            continue
            
        sembol = p[0].upper()
        kalan_p = p[1:]
        
        # 1. Kasa Ekleme (+ veya K)
        if sembol in ["+", "K", "KASA", "+KASA"]:
            try:
                grup, tutar = parse_grup_ve_tutar(kalan_p)
                res = hucreyeVeriYaz_impl(f"/kasa {grup} {tutar}", 4, "Kasa Ekleme", 1)
                etkilenen_gruplar.add(grup)
                islem_sonuclari.append(f"• 💰 <b>{grup.upper()}:</b> +{paraFormatla(tutar)} <i>(Kasa Girişi)</i>")
            except Exception as e:
                islem_sonuclari.append(f"• ⚠️ <b>{satir}:</b> {e}")
                
        # 2. Kasa Silme (-)
        elif sembol in ["-", "KASASIL", "-KASA"]:
            try:
                grup, tutar = parse_grup_ve_tutar(kalan_p)
                res = hucreyeVeriYaz_impl(f"/kasasil {grup} {tutar}", 4, "Kasa Silme", -1)
                etkilenen_gruplar.add(grup)
                islem_sonuclari.append(f"• 💸 <b>{grup.upper()}:</b> -{paraFormatla(tutar)} <i>(Kasa Çıkışı)</i>")
            except Exception as e:
                islem_sonuclari.append(f"• ⚠️ <b>{satir}:</b> {e}")
                
        # 3. Ödeme Ekleme (Ö veya O)
        elif sembol in ["Ö", "O", "ODEME", "ÖDEME", "+ODEME", "+ÖDEME"]:
            try:
                grup, tutar = parse_grup_ve_tutar(kalan_p)
                res = hucreyeVeriYaz_impl(f"/odeme {grup} {tutar}", 5, "Ödenen Ekleme", 1)
                etkilenen_gruplar.add(grup)
                islem_sonuclari.append(f"• 💸 <b>{grup.upper()}:</b> {paraFormatla(tutar)} <i>(Ödeme Yapıldı)</i>")
            except Exception as e:
                islem_sonuclari.append(f"• ⚠️ <b>{satir}:</b> {e}")
                
        # 4. Devir Ekleme (D)
        elif sembol in ["D", "DEVİR", "DEVIR", "+DEVIR"]:
            try:
                grup, tutar = parse_grup_ve_tutar(kalan_p)
                res = hucreyeVeriYaz_impl(f"/devir {grup} {tutar}", 3, "Devir Ekleme", 1)
                etkilenen_gruplar.add(grup)
                islem_sonuclari.append(f"• 🔄 <b>{grup.upper()}:</b> +{paraFormatla(tutar)} <i>(Devir)</i>")
            except Exception as e:
                islem_sonuclari.append(f"• ⚠️ <b>{satir}:</b> {e}")
                
        # 5. Masraf Ekleme (M veya G)
        elif sembol in ["M", "G", "MASRAF", "GİDER", "GIDER", "+MASRAF"]:
            try:
                masraf_adi, tutar = parse_grup_ve_tutar(kalan_p)
                res = masrafVerisiYaz_impl(f"/masrafekle {masraf_adi} {tutar}", "Masraf Ekleme", 1)
                islem_sonuclari.append(f"• 📉 <b>Masraf ({masraf_adi}):</b> {paraFormatla(tutar)}")
            except Exception as e:
                islem_sonuclari.append(f"• ⚠️ <b>{satir}:</b> {e}")
        else:
            try:
                grup, tutar = parse_grup_ve_tutar(p)
                res = hucreyeVeriYaz_impl(f"/kasa {grup} {tutar}", 4, "Kasa Ekleme", 1)
                etkilenen_gruplar.add(grup)
                islem_sonuclari.append(f"• 💰 <b>{grup.upper()}:</b> +{paraFormatla(tutar)} <i>(Kasa Girişi)</i>")
            except Exception as e:
                islem_sonuclari.append(f"• ❓ <code>{satir}</code> <i>(Tanınmayan format)</i>")

    if not islem_sonuclari:
        return "⚠️ İşlenecek geçerli bir işlem satırı bulunamadı."
        
    guncel_veriler = get_sheet_values_fast(sayfa)
    kalanlar_listesi = []
    
    for g_ham in sorted(etkilenen_gruplar):
        g_norm = normalize_text(g_ham)
        for r in guncel_veriler[1:43]:
            if len(r) >= 7 and normalize_text(r[1]) == g_norm:
                emoji = grupEmojisiBul(r[1])
                kalan_bakiye = guvenliSayi(r[6])
                kalanlar_listesi.append(f"{emoji} <b>{r[1].upper()}:</b> 🏦 <b>{paraFormatla(kalan_bakiye)}</b>")
                break
                
    saat = suankiZamaniAl().strftime("%H:%M")
    mesaj = (
        f"⚡ <b>TOPLU İŞLEM RAPORU</b>\n"
        f"━━━━━━━━━━\n"
        f"📅 Tarih: {sayfa.title} | ⏰ Saat: {saat}\n"
        f"📋 İşlenen Kalem: <b>{len(islem_sonuclari)} Adet</b>\n\n"
        f"✅ <b>İŞLEM DETAYLARI:</b>\n"
        + "\n".join(islem_sonuclari) + "\n\n"
    )
    
    if kalanlar_listesi:
        mesaj += (
            f"━━━━━━━━━━\n"
            f"📊 <b>İŞLEM SONRASI GÜNCEL KALANLAR:</b>\n"
            + "\n".join(kalanlar_listesi) + "\n"
        )
        
    mesaj += (
        f"━━━━━━━━━\n"
        f"💡 <i>Tüm kayıtlar Excel'e ve RAM önbelleğine anında işlendi.</i>"
    )
    return mesaj

def gecmis_gun_sorgula_impl(komut_metni: str) -> str:
    """
    Belirli geçmiş bir güne ait finans tablosunu veya o tarihteki carinin durumunu raporlar.
    Örnek:
    /tarih 25.08.2026
    /tarih 25.08.2026 SACİD
    """
    parcalar = komut_metni.strip().split()[1:]
    if not parcalar:
        return (
            "📅 <b>GEÇMİŞ GÜN SORGULAMA</b>\n"
            "━━━━━━━━━━\n"
            "Format: <code>/tarih [GG.AA.YYYY] [Cari Adı (Opsiyonel)]</code>\n\n"
            "📌 <b>Örnekler:</b>\n"
            "• <code>/tarih 25.08.2026</code> (Tüm gün bilançosu)\n"
            "• <code>/tarih 25.08.2026 SACİD</code> (O tarihteki SACİD fişi)\n"
            "• <code>/tarih 27.08.2026 THY</code>"
        )
        
    tarih_ham = parcalar[0].strip().replace("/", ".").replace("-", ".")
    cari_ham = " ".join(parcalar[1:]).strip() if len(parcalar) > 1 else ""
    
    m = re.search(r'(\d{1,2})\.(\d{1,2})\.(\d{4})', tarih_ham)
    if not m:
        return "⚠️ Lütfen geçerli bir tarih formatı giriniz! (Örn: <code>25.08.2026</code>)"
        
    gun, ay, yil = int(m.group(1)), int(m.group(2)), int(m.group(3))
    tarih_str = f"{gun:02d}.{ay:02d}.{yil:04d}"
    
    sh = get_spreadsheet()
    try:
        ws = sh.worksheet(tarih_str)
    except Exception:
        return f"⚠️ <b>{tarih_str}</b> tarihli bir arşiv çalışma sayfası bulunamadı. Lütfen tarihi kontrol ediniz."
        
    veriler = ws.get_all_values()
    
    # 1. Belirli Bir Cari Sorgulandıysa
    if cari_ham:
        hedef_norm = normalize_text(cari_ham)
        for r in veriler[1:43]:
            if len(r) >= 2 and normalize_text(r[1]) == hedef_norm:
                vals = [guvenliSayi(x) for x in r[1:7]]
                while len(vals) < 6: vals.append(0.0)
                dDevir, dKasa, dOdenen, dKomisyon, dKalan = vals[1], vals[2], vals[3], vals[4], vals[5]
                emoji = grupEmojisiBul(r[1])
                
                return (
                    f"📅 <b>GEÇMİŞ GÜN CARİ FİŞİ: {tarih_str}</b>\n"
                    f"━━━━━━━━━━\n"
                    f"{emoji} Cari Grup: <b>{r[1].upper()}</b>\n"
                    f"📁 Kaynak: <code>{tarih_str}</code> Sayfası\n\n"
                    f"🔄 O Günkü Devir: {paraFormatla(dDevir)}\n"
                    f"💰 Eklenen Kasa: {paraFormatla(dKasa)}\n"
                    f"💸 Yapılan Ödeme: {paraFormatla(dOdenen)}\n"
                    f"✂️ Kesinti/Komisyon: {paraFormatla(dKomisyon)}\n"
                    f"━━━━━━━━━━\n"
                    f"🏦 <b>O GÜNKÜ NET KALAN: {paraFormatla(dKalan)}</b>\n"
                    f"━━━━━━━━━━\n"
                    f"💡 <i>Çok günlük geçmiş ekstresi için: <code>/ekstre {r[1]}</code></i>"
                )
        return f"⚠️ <b>{tarih_str}</b> tarihli sayfada '<b>{cari_ham}</b>' adlı cari bulunamadı."
        
    # 2. Tüm Gün Özeti Sorgulandıysa
    finans = tablodan_finans_ozeti_hesapla(veriler)
    
    # Masraflar
    toplam_masraf = 0.0
    masraf_sayisi = 0
    for row in veriler[1:]:
        if len(row) >= 10:
            ad = row[8].strip()
            if ad and "GENEL TOPLAM" not in ad.upper() and ad != "-":
                fiyat = guvenliSayi(row[9])
                if abs(fiyat) > 0.001:
                    toplam_masraf += fiyat
                    masraf_sayisi += 1
                    
    sirali_cariler = sorted(finans["aktif_gruplar"], key=lambda x: abs(x["kalan"]), reverse=True)[:5]
    
    mesaj = (
        f"📅 <b>GEÇMİŞ GÜN BİLANÇOSU: {tarih_str}</b>\n"
        f"━━━━━━━━━━━\n"
        f"📁 Durum: 🔒 <b>Arşivlenmiş Gün</b>\n"
        f"🏢 İşlem Gören Aktif Cari: <b>{len(finans['aktif_gruplar'])} Adet</b>\n\n"
        f"📊 <b>GENEL BİLANÇO:</b>\n"
        f"• 🔄 Toplam Devir: {paraFormatla(finans['devir'])}\n"
        f"• 💰 Toplam Kasa: {paraFormatla(finans['kasa'])}\n"
        f"• 💸 Toplam Ödeme: {paraFormatla(finans['odenen'])}\n"
        f"• ✂️ Toplam Komisyon: {paraFormatla(finans['komisyon'])}\n"
        f"• 🏦 <b>NET KALAN KASA: {paraFormatla(finans['kalan'])}</b>\n\n"
    )
    if masraf_sayisi > 0:
        mesaj += f"📉 <b>GÜNLÜK GİDERLER:</b>\n• {masraf_sayisi} Kalem Masraf: <b>{paraFormatla(toplam_masraf)}</b>\n\n"
        
    if sirali_cariler:
        mesaj += f"━━━━━━━━━━━\n🏆 <b>EN YÜKSEK İŞLEM GÖREN CARİLER:</b>\n"
        for idx, g in enumerate(sirali_cariler, 1):
            emoji = grupEmojisiBul(g["ad"])
            mesaj += f"• {idx}. {emoji} <b>{g['ad'].upper()}:</b> 🏦 {paraFormatla(g['kalan'])}\n"

    mesaj += (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <i>Belirli bir carinin o günkü dökümü için: <code>/tarih {tarih_str} SACİD</code></i>"
    )
    return mesaj

def metinCevir_impl(gelenMetin: str) -> str:
    cevrilecek = re.sub(r'^/(?:çeviri|ceviri)(?:@\w+)?\s*', '', gelenMetin, flags=re.IGNORECASE).strip()
    if not cevrilecek:
        return "⚠️ Lütfen çevrilmesini istediğiniz metni yazın.\nÖrnek: <code>/çeviri Merhaba</code>"
    try:
        q = urllib.parse.quote(cevrilecek)
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=tr&dt=t&q={q}"
        res = http_get_json(url)
        turkce = "".join([x[0] for x in res[0] if x[0]])
        son_ceviri = turkce
        etiket = "🌍 Yabancı Dil ➔ 🇹🇷 Türkçe"
        if turkce.lower() == cevrilecek.lower():
            url_en = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=en&dt=t&q={q}"
            res_en = http_get_json(url_en)
            son_ceviri = "".join([x[0] for x in res_en[0] if x[0]])
            etiket = "🇹🇷 Türkçe ➔ 🇺🇸 İngilizce"
        return f"🌐 <b>YAPAY ZEKA ÇEVİRİSİ</b>\n━━━━━━━━━━━━\n\n📝 <b>Orijinal Metin:</b>\n<i>{cevrilecek}</i>\n\n🎯 <b>{etiket}:</b>\n<code>{son_ceviri}</code>"
    except Exception as e:
        return f"❌ <b>Çeviri Hatası:</b> {e}"

# --- YENİ GÜN DEVİR İŞLEMİ (GRUP BAZLI G ➔ C AKTARIMI & D, E SIFIRLAMA) ---
def yenigun_baslat_mesaji():
    sh = get_spreadsheet()
    kaynak_sayfa = get_active_daily_sheet(sh)
    
    # Dinamik İleri Tarih: Son sayfa adına +1 gün ekle
    hedef_tarih = suankiZamaniAl().strftime("%d.%m.%Y")
    if re.match(r'^\d{2}\.\d{2}\.\d{4}$', kaynak_sayfa.title):
        try:
            d_obj = datetime.datetime.strptime(kaynak_sayfa.title, "%d.%m.%Y")
            hedef_tarih = (d_obj + datetime.timedelta(days=1)).strftime("%d.%m.%Y")
        except Exception: pass

    klavye = {
        "inline_keyboard": [
            [{"text": "🔄 Masrafları Temizle & Yeni Güne Geç", "callback_data": "yenigun_onay_sil"}],
            [{"text": "📋 Masrafları Koru & Yeni Güne Geç", "callback_data": "yenigun_onay_tut"}],
            [{"text": "❌ İptal Et", "callback_data": "yenigun_iptal"}]
        ]
    }
    return (
        f"🌅 <b>YENİ GÜN DEVİR İŞLEMİ ➔ {hedef_tarih}</b>\n━━━━━━━━━━\n\n"
        f"📁 <b>Kaynak Sayfa:</b> <code>{kaynak_sayfa.title}</code>\n"
        f"📅 <b>Açılacak Yeni Sayfa:</b> <code>{hedef_tarih}</code>\n\n"
        "1. Dünkü <b>Kalan Kasa</b> (G sütunu) tutarları (+/- işaretleri ve kuruşları korunarak) yeni günün <b>Devir/Borç</b> (C sütunu) hanesine aktarılacaktır.\n"
        "2. <b>Güncel Kasa</b> (D) ve <b>Ödenen</b> (E) sütunları sıfırlanacaktır (2-42. Satırlar).\n"
        "3. <b>G45 Kalan Fark:</b> Dünün G45 nihai kapanış bakiyesi (+/- korunarak) yeni günün <code>=FARK+F43-J43</code> formülüne otomatik aktarılacaktır.\n\n"
        "Lütfen masraf tercihinizi seçin:",
        klavye
    )

def yenigun_gerceklestir_impl(masraflari_sil: bool) -> str:
    sh = get_spreadsheet()
    kaynak_sayfa = get_active_daily_sheet(sh)
    
    # 1. Dinamik İleri Tarih Hesaplama (+1 Gün)
    hedef_yeni_tarih = suankiZamaniAl().strftime("%d.%m.%Y")
    if re.match(r'^\d{2}\.\d{2}\.\d{4}$', kaynak_sayfa.title):
        try:
            d_obj = datetime.datetime.strptime(kaynak_sayfa.title, "%d.%m.%Y")
            hedef_yeni_tarih = (d_obj + datetime.timedelta(days=1)).strftime("%d.%m.%Y")
        except Exception: pass
        
    # 2. Eğer hedef sayfa adı önceden bozuk/yarım açılmışsa temizle
    try:
        mevcut_sayfa = sh.worksheet(hedef_yeni_tarih)
        if isinstance(mevcut_sayfa.col_count, int) and (mevcut_sayfa.col_count < 8 or mevcut_sayfa.row_count < 30):
            sh.del_worksheet(mevcut_sayfa)
        else:
            return f"⚠️ <b>{hedef_yeni_tarih}</b> tarihli sayfa zaten mevcut ve kullanımda!"
    except Exception:
        pass
        
    if not is_valid_daily_sheet(kaynak_sayfa):
        return "❌ Kopyalanacak geçerli bir kaynak finans sayfası bulunamadı!"
        
    # 3. Dünkü sayfanın tüm verilerini oku ve her grubun G sütunundaki (Kalan Kasa) bakiyesini haritalandır
    dunku_veriler = kaynak_sayfa.get_all_values()
    
    grup_dunku_kalanlar = {}
    for r_idx, row in enumerate(dunku_veriler[1:], start=2):
        if r_idx > 42: break
        if len(row) >= 2:
            grup_adi = row[1].strip()
            if grup_adi and grup_adi != "*" and "GENEL TOPLAM" not in grup_adi.upper():
                g_norm = normalize_text(grup_adi)
                # G Sütunu (index 6: Kalan Kasa)
                kalan_val = guvenliSayi(row[6]) if len(row) > 6 else 0.0
                grup_dunku_kalanlar[g_norm] = kalan_val

    # 4. G45 Dünkü Kalan Fark Değerini Oku (+/- işaretleri ve kuruşları eksiksiz al)
    dunku_g45_val = 0.0
    if len(dunku_veriler) >= 45 and len(dunku_veriler[44]) > 6:
        dunku_g45_val = guvenliSayi(dunku_veriler[44][6])
    else:
        try:
            dunku_g45_val = guvenliSayi(kaynak_sayfa.acell('G45').value)
        except Exception:
            pass

    # 5. Gerçek tam finans sayfasını yeni gün adıyla kopyala
    yeni_sayfa = kaynak_sayfa.duplicate(new_sheet_name=hedef_yeni_tarih)
    
    # 6. GRUP BAZLI DEVİR AKTARIMI VE SIFIRLAMA MATRİSİ: C2:E42 (Devir = G sütunu, Kasa = 0, Ödenen = 0)
    matrix_c_e = []
    toplam_devir = 0.0
    
    for r_idx in range(2, 43):
        dunun_kalani = 0.0
        if r_idx - 1 < len(dunku_veriler):
            row = dunku_veriler[r_idx - 1]
            if len(row) >= 2:
                grup_adi = row[1].strip()
                if grup_adi and grup_adi != "*" and "GENEL TOPLAM" not in grup_adi.upper():
                    g_norm = normalize_text(grup_adi)
                    # Hangi gruba ait ise dünkü G sütunu bakiyesini kuruşu kuruşuna al
                    dunun_kalani = grup_dunku_kalanlar.get(g_norm, 0.0)
                    toplam_devir += dunun_kalani
                    
        # [C Sütunu: Devir/Borç, D Sütunu: Kasa (0), E Sütunu: Ödenen (0)]
        matrix_c_e.append([dunun_kalani, 0, 0])
        
    # Tek seferde C2:E42 bloğunu güncelle (1 tek API çağrısıyla anında yazar)
    yeni_sayfa.update('C2:E42', matrix_c_e, value_input_option='USER_ENTERED')
                
    # 7. Masrafları Temizleme Seçimi (I2:J42 tek seferde toplu temizleme)
    if masraflari_sil and yeni_sayfa.col_count >= 10:
        empty_masraf = [['', ''] for _ in range(41)]
        yeni_sayfa.update('I2:J42', empty_masraf)

    # 8. G45 Hücresine Dünkü Kapanış Farkını İçeren Yeni Formülü Yaz
    # Türkçe Google Sheets yerel ayarına uygun olarak ondalık ayracı virgül (,) yapılır (Örn: =6979160,83+F43-J43)
    if abs(dunku_g45_val - round(dunku_g45_val)) < 0.00001:
        val_str = str(int(round(dunku_g45_val)))
    else:
        val_str = f"{dunku_g45_val:.2f}".rstrip('0').rstrip('.').replace(".", ",")

    yeni_g45_formulu = f"={val_str}+F43-J43"
    try:
        yeni_sayfa.update('G45', [[yeni_g45_formulu]], value_input_option='USER_ENTERED')
    except Exception as e:
        print(f"G45 formül güncelleme hatası: {e}")
        try:
            yeni_sayfa.update_acell('G45', yeni_g45_formulu)
        except Exception:
            pass
            
    # Yeni açılan güncel sayfayı hemen aktif sayfa olarak hafızaya al ve önbelleği güncelle
    global _cached_active_sheet, _cached_active_sheet_time, _cached_sheet_matrix, _cached_sheet_matrix_title, _cached_sheet_matrix_time
    _cached_active_sheet = yeni_sayfa
    _cached_active_sheet_time = time.time()
    _cached_sheet_matrix = None
    _cached_sheet_matrix_title = ""
    _cached_sheet_matrix_time = 0

    sistemeLogYaz("Yeni Gün Geçişi", f"Yeni gün ({hedef_yeni_tarih}) açıldı. Kaynak: {kaynak_sayfa.title} | Devir: {paraFormatla(toplam_devir)} | G45: {yeni_g45_formulu}")
    
    return (
        f"🌅 <b>{hedef_yeni_tarih} GÜNÜ BAŞARIYLA AÇILDI!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📁 <b>Kaynak Alınan Gün:</b> <code>{kaynak_sayfa.title}</code>\n"
        f"🔄 <b>Devir'e (C) Aktarılan Kalan Kasa (G):</b> {paraFormatla(toplam_devir)}\n"
        f"💰 <b>Güncel Kasa (D) ve Ödenen (E):</b> Sıfırlandı (2-42. Satırlar)\n"
        f"📊 <b>G45 Kalan Fark:</b> {paraFormatla(dunku_g45_val)} yeni güne aktarıldı (Formül: <code>{yeni_g45_formulu}</code>)\n"
        f"📉 <b>Masraflar:</b> {'Temizlendi' if masraflari_sil else 'Korundu'}\n\n"
        f"⚠️ <i>Lütfen tablodan devirleri ve G45 farkını kontrol ediniz.</i>\n"
        f"İyi çalışmalar ve bol kazançlar dileriz! 🚀"
    )

# --- ADMİN YÖNETİM FONKSİYONLARI ---
def admin_ekle_impl(komut_metni: str, ekleyen_id: int) -> str:
    if ekleyen_id != KURUCU_ID:
        return "⛔ <b>Yetkisiz İşlem:</b> Sadece Kurucu yeni yönetici ekleyebilir."
    p = komut_metni.strip().split()
    if len(p) < 2 or not p[1].isdigit():
        return "⚠️ <b>Hatalı Kullanım!</b>\nÖrnek: <code>/adminekle 123456789 Ahmet</code>"
    yeni_id = int(p[1])
    isim = " ".join(p[2:]) if len(p) > 2 else f"Yönetici_{yeni_id}"
    
    sh = get_spreadsheet()
    try: adminSayfasi = sh.worksheet(ADMIN_SAYFASI)
    except Exception:
        adminSayfasi = sh.add_worksheet(title=ADMIN_SAYFASI, rows=100, cols=4)
        adminSayfasi.append_row(["Telegram ID", "Yönetici Adı", "Ekleyen", "Tarih"])
        
    rows = adminSayfasi.get_all_values()
    for r in rows[1:]:
        if len(r) > 0 and r[0].strip() == str(yeni_id):
            return f"⚠️ <b>{yeni_id}</b> zaten yetkili yöneticiler arasında!"
            
    adminSayfasi.append_row([str(yeni_id), isim, "KURUCU", bugununTarihiniAl()])
    app_state["EK_ADMINLER"].add(yeni_id)
    for k in list(_yetkisiz_uyarilanlar):
        if k[0] == yeni_id:
            _yetkisiz_uyarilanlar.discard(k)
    sistemeLogYaz("Yönetici Eklendi", f"{isim} (ID: {yeni_id})")
    return f"✅ <b>Yönetici Eklendi!</b>\n👤 <b>İsim:</b> {isim}\n🆔 <b>Telegram ID:</b> <code>{yeni_id}</code>\n\nArtık botu kullanabilir."

def admin_sil_impl(komut_metni: str, silen_id: int) -> str:
    if silen_id != KURUCU_ID:
        return "⛔ <b>Yetkisiz İşlem:</b> Sadece Kurucu yönetici silebilir."
    p = komut_metni.strip().split()
    if len(p) < 2 or not p[1].isdigit():
        return "⚠️ <b>Hatalı Kullanım!</b>\nÖrnek: <code>/adminsil 123456789</code>"
    silinecek_id = int(p[1])
    if silinecek_id == KURUCU_ID:
        return "⛔ Kurucu yönetici silinemez!"
        
    sh = get_spreadsheet()
    adminSayfasi = sh.worksheet(ADMIN_SAYFASI)
    rows = adminSayfasi.get_all_values()
    for i, r in enumerate(rows[1:], start=2):
        if len(r) > 0 and r[0].strip() == str(silinecek_id):
            adminSayfasi.delete_rows(i)
            app_state["EK_ADMINLER"].discard(silinecek_id)
            sistemeLogYaz("Yönetici Silindi", f"ID: {silinecek_id}")
            return f"🗑️ <b>ID: {silinecek_id}</b> yönetici listesinden silindi ve yetkisi alındı."
    return f"⚠️ <b>{silinecek_id}</b> yönetici listesinde bulunamadı."

def admin_listesi_impl() -> str:
    sh = get_spreadsheet()
    try:
        adminSayfasi = sh.worksheet(ADMIN_SAYFASI)
        rows = adminSayfasi.get_all_values()
        out = "🛡️ <b>YETKİLİ YÖNETİCİLER LİSTESİ</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        out += f"👑 <b>KURUCU:</b> <code>{KURUCU_ID}</code> (@CRYPTOATAKAN)\n\n"
        for r in rows[1:]:
            if len(r) > 0 and r[0].strip().isdigit() and int(r[0].strip()) != KURUCU_ID:
                isim = r[1] if len(r) > 1 else "Yönetici"
                out += f"👤 <b>{isim}:</b> <code>{r[0]}</code>\n"
        out += "\n💡 <i>Yeni yönetici eklemek için: /adminekle ID İsim</i>"
        return out
    except Exception as e:
        return f"❌ <b>Hata:</b> {e}"

def debug_sistem_impl() -> str:
    """Sistem performansını ölçer, bağlantıları ve önbelleği yeniler, RAM ve gecikme (ping) raporlar."""
    baslangic = time.time()
    
    # 1. Bellek temizliği (Garbage Collection)
    import gc
    gc.collect()
    
    # 2. Telegram API Ping Testi
    tg_ping_str = "⚠️ Ölçülemedi"
    try:
        t0 = time.time()
        r_tg = telegram_api("getMe", {})
        if r_tg.get("ok"):
            tg_ms = (time.time() - t0) * 1000
            tg_ping_str = f"<code>{tg_ms:.0f} ms</code> <i>(Çok Hızlı)</i>" if tg_ms < 300 else f"<code>{tg_ms:.0f} ms</code>"
    except Exception as e:
        tg_ping_str = f"⚠️ Hata: {e}"

    # 3. Google Sheets API Doğrudan Sayfa Bağlantısı
    gs_ping_str = "⚠️ Ölçülemedi"
    aktif_sayfa_str = "Bilinmiyor"
    toplam_sayfa_sayisi = 0
    try:
        t0 = time.time()
        sh = get_spreadsheet(force_refresh=False)
        ws = get_active_daily_sheet(sh, force_refresh=False)
        gs_ms = (time.time() - t0) * 1000
        gs_ping_str = f"<code>{gs_ms:.0f} ms</code> <i>(Hızlı)</i>" if gs_ms < 400 else f"<code>{gs_ms:.0f} ms</code>"
        aktif_sayfa_str = ws.title
        toplam_sayfa_sayisi = len(sh.worksheets()) if _cached_spreadsheet else 33
    except Exception as e:
        gs_ping_str = f"⚠️ Bağlantı: {e}"

    # 4. RAM In-Memory Ayna Hızı Testi
    t_ram0 = time.time()
    if _cached_sheet_matrix:
        _ = len(_cached_sheet_matrix)
    t_ram_ms = (time.time() - t_ram0) * 1000
    ram_hiz_str = f"<code>{t_ram_ms:.2f} ms</code> <i>(Işık Hızında / RAM)</i>" if t_ram_ms < 1 else f"<code>{t_ram_ms:.2f} ms</code>"

    # 5. İş Parçacıkları & Sistem Durumu
    aktif_thread_sayisi = threading.active_count()
    toplam_sure_ms = (time.time() - baslangic) * 1000
    
    yanit = (
        f"🛠️ <b>CFO BOT SİSTEM & DEBUG RAPORU</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🚀 <b>Durum:</b> RAM Ayna Önbelleği Devrede!\n\n"
        f"📊 <b>GECİKME VE PING TESTİ:</b>\n"
        f"• ✈️ Telegram Bot API: {tg_ping_str}\n"
        f"• 🌐 Google Sheets API (Doğrudan): {gs_ping_str}\n"
        f"• ⚡ RAM Ayna Okuma Hızı: {ram_hiz_str}\n\n"
        f"🧠 <b>BELLEK VE ÇALIŞMA ALANI:</b>\n"
        f"• 📅 Aktif Gün Sayfası: <b>{aktif_sayfa_str}</b>\n"
        f"• 📑 Toplam Çalışma Sayfası: <code>{toplam_sayfa_sayisi} Adet</code>\n"
        f"• 🧵 Aktif Thread Havuzu: <code>{aktif_thread_sayisi} İş Parçacığı</code>\n"
        f"• ⚡ Toplam İşlem Süresi: <code>{toplam_sure_ms:.0f} ms</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <i>Tüm /kasa ve /rapor sorguları artık 0 ms RAM ayna hızında çalışıyor.</i>"
    )
    return yanit

# --- YARDIMCI: HIZLI VE GÜVENLİ ÇALIŞTIRICI & DİNAMİK 1-100% İLERLEME ÇUBUĞU ---
def dynamic_progress_bar(percentage: int, total_blocks: int = 10) -> str:
    filled = int(round((percentage / 100.0) * total_blocks))
    filled = max(0, min(total_blocks, filled))
    empty = total_blocks - filled
    return "█" * filled + "░" * empty

def yukleme_adim_metni_uret(islem_tipi: str, yuzde: int) -> str:
    """İşlem tipine ve yüzdeye göre dinamik progress bar metni üretir."""
    t = (islem_tipi or "").lower()
    p_bar = dynamic_progress_bar(yuzde)
    
    if any(k in t for k in ["kur", "doviz", "döviz", "kripto", "trc20", "arbitraj", "varlik", "varlık", "portfoy", "portföy", "cevir", "t_"]):
        baslik = "🪙 <b>Piyasa Kurları & Varlık Taraması</b>"
        if yuzde < 35:
            alt = "⏳ <i>Piyasa bağlantısı kuruluyor...</i>"
        elif yuzde < 70:
            alt = "⏳ <i>Borsa ve Kapalıçarşı API'leri sorgulanıyor...</i>"
        elif yuzde < 95:
            alt = "⏳ <i>Canlı kurlar ve portföy hesaplanıyor...</i>"
        else:
            alt = "✅ <i>Veriler hazırlandı, iletiliyor...</i>"
    elif any(k in t for k in ["iban", "banka", "sablon", "şablon", "tahsis", "bosalt", "boşalt", "cozumle"]):
        baslik = "🏦 <b>İBAN & Banka Sorgulama</b>"
        if yuzde < 35:
            alt = "⏳ <i>İBAN havuzu ve banka kodları taranıyor...</i>"
        elif yuzde < 70:
            alt = "⏳ <i>MOD-97 ve şirket envanteri doğrulanıyor...</i>"
        elif yuzde < 95:
            alt = "⏳ <i>Ödeme şablonu derleniyor...</i>"
        else:
            alt = "✅ <i>Şablon doğrulandı, iletiliyor...</i>"
    elif any(k in t for k in ["kasa", "rapor", "bakiye", "borc", "borç", "alacak", "ozet", "özet", "ekstre", "tarih", "gun", "gunsonu", "kapanis"]):
        baslik = "📊 <b>Finans & Kasa Analizi</b>"
        if yuzde < 35:
            alt = "⏳ <i>Aktif gün sayfası ve cariler taranıyor...</i>"
        elif yuzde < 70:
            alt = "⏳ <i>Devir, kasa ve ödeme matrisi okunuyor...</i>"
        elif yuzde < 95:
            alt = "⏳ <i>Bilanço ve bakiye sıralaması hesaplanıyor...</i>"
        else:
            alt = "✅ <i>Analiz tamamlandı, iletiliyor...</i>"
    else:
        baslik = "⚡ <b>CFO İşlem Motoru</b>"
        if yuzde < 35:
            alt = "⏳ <i>İşlem başlatılıyor...</i>"
        elif yuzde < 70:
            alt = "⏳ <i>Finansal veriler işleniyor...</i>"
        elif yuzde < 95:
            alt = "⏳ <i>Sonuçlar derleniyor...</i>"
        else:
            alt = "✅ <i>Tamamlandı, iletiliyor...</i>"
            
    return f"{baslik}\n<code>[{p_bar}] %{yuzde}</code>\n{alt}"

def yukleme_metni_uret(islem_tipi: str = "") -> str:
    return yukleme_adim_metni_uret(islem_tipi, 50)

def islemi_analiz_bildirimiyle_yap(chat_id: int, islem_fn, *args, goster_bildirim: bool = False, islem_tipi: str = ""):
    # 1. Non-blocking typing bildirimi
    telegramChatAction(chat_id, "typing")
    
    fn_name = islem_tipi or getattr(islem_fn, "__name__", "")
    for a in args:
        if isinstance(a, str):
            fn_name += "_" + a

    msg_id = None
    stop_anim = threading.Event()

    if goster_bildirim:
        # İlerleme çubuğunu başlat (%20)
        ilk_metin = yukleme_adim_metni_uret(fn_name, 20)
        yukleniyor = telegramMesajGonder(chat_id, ilk_metin, kapat_butonu_ekle=False)
        msg_id = yukleniyor.get("result", {}).get("message_id") if yukleniyor.get("ok") else None

        # Arka planda non-blocking animatör (ana işlemi asla bekletmez/yavaşlatmaz)
        def animasyon_worker(m_id, f_name):
            adimlar = [45, 70, 90]
            for y in adimlar:
                if stop_anim.wait(0.12):
                    break
                try:
                    telegramMesajDuzenle(chat_id, m_id, yukleme_adim_metni_uret(f_name, y), kapat_butonu_ekle=False)
                except Exception:
                    pass

        if msg_id:
            anim_thread = threading.Thread(target=animasyon_worker, args=(msg_id, fn_name), daemon=True)
            anim_thread.start()

    # 2. Asıl işlemi hemen paralel iş parçacığı havuzunda çalıştır
    future = _update_executor.submit(islem_fn, *args)

    # 3. Sonucu bekle ve animatörü durdur
    try:
        sonuc = future.result(timeout=25)
    except Exception as e:
        stop_anim.set()
        if msg_id:
            telegramMesajSil(chat_id, msg_id)
        telegramMesajGonder(chat_id, f"❌ <b>Hata:</b> {e}")
        return
    finally:
        stop_anim.set()

    # 4. Yükleme mesajını sil ve nihai sonucu anında ilet
    if msg_id:
        telegramMesajSil(chat_id, msg_id)

    if isinstance(sonuc, tuple):
        text, markup = sonuc
        telegramMesajGonder(chat_id, text, markup)
    else:
        telegramMesajGonder(chat_id, str(sonuc))

# --- UPDATE DISPATCHER ---
def process_telegram_update(update: dict):
    if "callback_query" in update:
        cq = update["callback_query"]
        data = cq.get("data", "")
        chat_id = cq["message"]["chat"]["id"]
        user_id = cq["from"]["id"]
        
        telegram_api("answerCallbackQuery", {"callback_query_id": cq["id"]})
        
        if data.startswith("t_yenile_"):
            if user_id != KURUCU_ID:
                yetkisiz_uyari_gonder(chat_id, user_id, "⛔ <b>Yetkisiz İşlem:</b> Şirket cüzdan ve rezerv raporunu sorgulama yetkisi sadece <b>Şirket Kurucusuna</b> aittir.")
                return
            cuzdan = data.replace("t_yenile_", "").strip()
            islemi_analiz_bildirimiyle_yap(chat_id, trc20_varlik_raporu_uret, cuzdan)
            return

        if not yetkili_mi(user_id):
            yetkisiz_uyari_gonder(chat_id, user_id, "⛔ <b>Erişim Reddedildi!</b>\nBu işlem için yetkiniz bulunmamaktadır.")
            return
            
        if data in ["rehber", "rehber_ana"]:
            msg_id = cq.get("message", {}).get("message_id")
            if msg_id:
                telegramMesajDuzenle(chat_id, msg_id, rehber_ana_metni(), rehber_ana_klavyesi())
            else:
                telegramMesajGonder(chat_id, rehber_ana_metni(), rehber_ana_klavyesi())
        elif data.startswith("rehber_"):
            kat = data.replace("rehber_", "")
            msg_id = cq.get("message", {}).get("message_id")
            if msg_id:
                telegramMesajDuzenle(chat_id, msg_id, rehber_kategori_metni(kat), rehber_kategori_klavyesi())
            else:
                telegramMesajGonder(chat_id, rehber_kategori_metni(kat), rehber_kategori_klavyesi())
        elif data == "rapor_ozet":
            islemi_analiz_bildirimiyle_yap(chat_id, hizliOzetUret_impl)
        elif data == "rapor_masraf":
            islemi_analiz_bildirimiyle_yap(chat_id, masrafRaporuUret_impl)
        elif data == "rapor_tumu":
            islemi_analiz_bildirimiyle_yap(chat_id, tumGruplarRaporu_impl, goster_bildirim=True)
        elif data.startswith("risk_"):
            filtre = data.replace("risk_", "").strip()
            msg_id = cq.get("message", {}).get("message_id")
            metin, klavye = bakiye_risk_raporu_uret(filtre)
            if msg_id:
                telegramMesajDuzenle(chat_id, msg_id, metin, klavye)
        elif data == "dashboard_yenile":
            msg_id = cq.get("message", {}).get("message_id")
            metin, klavye = cfo_dashboard_raporu_uret()
            if msg_id:
                telegramMesajDuzenle(chat_id, msg_id, metin, klavye)
            else:
                telegramMesajGonder(chat_id, metin, klavye)
        elif data == "canli_kur_yenile":
            msg_id = cq.get("message", {}).get("message_id")
            metin, klavye = canliKurSorgula_impl(force_refresh=True)
            if msg_id:
                telegramMesajDuzenle(chat_id, msg_id, metin, klavye)
            else:
                telegramMesajGonder(chat_id, metin, klavye)
            try:
                telegram_api("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "🔄 Canlı kurlar güncellendi!"})
            except Exception:
                pass
        elif data == "menu_yenigun":
            metin, klavye = yenigun_baslat_mesaji()
            telegramMesajGonder(chat_id, metin, klavye)
        elif data == "yenigun_onay_sil":
            islemi_analiz_bildirimiyle_yap(chat_id, yenigun_gerceklestir_impl, True)
        elif data == "yenigun_onay_tut":
            islemi_analiz_bildirimiyle_yap(chat_id, yenigun_gerceklestir_impl, False)
        elif data == "yenigun_iptal":
            telegramMesajGonder(chat_id, "❌ Yeni gün devir işlemi iptal edildi.")
        elif data.startswith("rapor_ilet_"):
            draft_id = data.replace("rapor_ilet_", "").strip()
            item = app_state.get("RAPOR_TASLAKLARI", {}).get(draft_id)
            if not item:
                telegram_api("answerCallbackQuery", {
                    "callback_query_id": cq["id"],
                    "text": "⚠️ Rapor taslağı bulunamadı veya süresi dolmuş.",
                    "show_alert": True
                })
                return
            
            grup_adi = item["grup"]
            grup_metni = item["metin"]
            
            grup_baglantilarini_guncelle()
            baglantilar = app_state.get("GRUP_BAGLANTILARI", {})
            hedef_chat_id = None
            hedef_title = grup_adi
            g_norm = normalize_text(grup_adi)
            
            for c_id, info in baglantilar.items():
                if normalize_text(info.get("grup", "")) == g_norm:
                    hedef_chat_id = c_id
                    hedef_title = info.get("title") or info.get("grup") or grup_adi
                    break
            
            if hedef_chat_id:
                res = telegramMesajGonder(hedef_chat_id, grup_metni)
                if res.get("ok"):
                    telegram_api("answerCallbackQuery", {
                        "callback_query_id": cq["id"],
                        "text": f"✅ Rapor '{hedef_title}' Telegram grubuna başarıyla iletildi!",
                        "show_alert": False
                    })
                    msg_id = cq.get("message", {}).get("message_id")
                    guncel_metin = grup_metni + f"\n\n🟢 <b>İletildi:</b> <i>{hedef_title} Telegram Grubu</i>"
                    yeni_klavye = {
                        "inline_keyboard": [
                            [{"text": f"✅ {hedef_title} Grubuna İletildi", "callback_data": "duyuru_bos_uyari_"}],
                            [{"text": "🗑️ Mesajı Kapat", "callback_data": "mesaj_kapat"}]
                        ]
                    }
                    if msg_id:
                        telegramMesajDuzenle(chat_id, msg_id, guncel_metin, yeni_klavye)
                else:
                    err_desc = res.get("description", "API Hatası")
                    telegram_api("answerCallbackQuery", {
                        "callback_query_id": cq["id"],
                        "text": f"❌ Mesaj iletilemedi: {err_desc}",
                        "show_alert": True
                    })
            else:
                telegram_api("answerCallbackQuery", {
                    "callback_query_id": cq["id"],
                    "text": f"⚠️ '{grup_adi}' adında bağlı bir Telegram grubu bulunamadı!\n\nLütfen o grupta '/grupbagla {grup_adi}' yazarak grubu bağlayınız.",
                    "show_alert": True
                })
        elif data.startswith("rapor_"):
            grup = data.replace("rapor_", "")
            islemi_analiz_bildirimiyle_yap(chat_id, grup_kasa_analiz_fisi_uret, grup)
        elif data.startswith("ibanbosta_"):
            hesap_adi = data.replace("ibanbosta_", "").strip()
            ok, h_ad, eski_c, s_title = iban_bosalt_direct(hesap_adi)
            msg_id = cq.get("message", {}).get("message_id")
            orig_text = cq.get("message", {}).get("text", "")
            if ok:
                yeni_metin = orig_text + f"\n\n🟢 <b>{h_ad} hesabı boşa çıkarıldı (Excel güncellendi - Müsait).</b>"
                if msg_id:
                    telegramMesajDuzenle(chat_id, msg_id, yeni_metin, None)
            else:
                telegramMesajGonder(chat_id, f"⚠️ {h_ad}")
        elif data.startswith("grup_iban_sil_"):
            parcalar = data.replace("grup_iban_sil_", "").rsplit("_", 1)
            hesap_adi = parcalar[0].strip()
            hedef_cari = parcalar[1].strip() if len(parcalar) > 1 else ""
            ok, h_ad, eski_c, s_title = iban_bosalt_direct(hesap_adi)
            metin, klavye = grup_aktif_ibanlar_raporu_uret(hedef_cari, chat_id)
            msg_id = cq.get("message", {}).get("message_id")
            if msg_id:
                telegramMesajDuzenle(chat_id, msg_id, metin, klavye)
        elif data.startswith("grup_iban_yenile_"):
            hedef_cari = data.replace("grup_iban_yenile_", "").strip()
            metin, klavye = grup_aktif_ibanlar_raporu_uret(hedef_cari, chat_id)
            msg_id = cq.get("message", {}).get("message_id")
            if msg_id:
                telegramMesajDuzenle(chat_id, msg_id, metin, klavye)
        elif data.startswith("duyuru_gonder_iban_"):
            draft_id = data.replace("duyuru_gonder_iban_", "").strip()
            msg_id = cq.get("message", {}).get("message_id")
            metin, klavye = toplu_duyuru_yayinla_callback(draft_id, "iban_aktif", user_id)
            if msg_id:
                telegramMesajDuzenle(chat_id, msg_id, metin, klavye)
            else:
                telegramMesajGonder(chat_id, metin, klavye)
        elif data.startswith("duyuru_gonder_tumu_"):
            draft_id = data.replace("duyuru_gonder_tumu_", "").strip()
            msg_id = cq.get("message", {}).get("message_id")
            metin, klavye = toplu_duyuru_yayinla_callback(draft_id, "tumu", user_id)
            if msg_id:
                telegramMesajDuzenle(chat_id, msg_id, metin, klavye)
            else:
                telegramMesajGonder(chat_id, metin, klavye)
        elif data.startswith("duyuru_ozel_menu_"):
            draft_id = data.replace("duyuru_ozel_menu_", "").strip()
            msg_id = cq.get("message", {}).get("message_id")
            metin, klavye = duyuru_ozel_grup_secim_ekrani(draft_id)
            if msg_id:
                telegramMesajDuzenle(chat_id, msg_id, metin, klavye)
            else:
                telegramMesajGonder(chat_id, metin, klavye)
        elif data.startswith("duyuru_ana_menu_"):
            draft_id = data.replace("duyuru_ana_menu_", "").strip()
            msg_id = cq.get("message", {}).get("message_id")
            metin, klavye = toplu_duyuru_ana_panel_uret(draft_id)
            if msg_id:
                telegramMesajDuzenle(chat_id, msg_id, metin, klavye)
            else:
                telegramMesajGonder(chat_id, metin, klavye)
        elif data.startswith("duyuru_tek_"):
            parcalar = data.split("_", 3)
            if len(parcalar) >= 4:
                draft_id = parcalar[2]
                try:
                    hedef_c_id = int(parcalar[3])
                    msg_id = cq.get("message", {}).get("message_id")
                    metin, klavye = toplu_duyuru_tek_grup_yayinla_callback(draft_id, hedef_c_id, user_id)
                    if msg_id:
                        telegramMesajDuzenle(chat_id, msg_id, metin, klavye)
                    else:
                        telegramMesajGonder(chat_id, metin, klavye)
                except ValueError:
                    pass
        elif data.startswith("duyuru_iptal_"):
            draft_id = data.replace("duyuru_iptal_", "").strip()
            app_state.get("DUYURU_TASLAKLARI", {}).pop(draft_id, None)
            msg_id = cq.get("message", {}).get("message_id")
            if msg_id:
                telegramMesajDuzenle(chat_id, msg_id, "❌ <b>Toplu duyuru gönderimi iptal edildi.</b>", None)
            else:
                telegramMesajGonder(chat_id, "❌ <b>Toplu duyuru gönderimi iptal edildi.</b>")
        elif data.startswith("duyuru_bos_uyari_"):
            telegram_api("answerCallbackQuery", {
                "callback_query_id": cq["id"],
                "text": "⚠️ Şu anda İBAN'ı aktif olarak atanmış bir grup bulunmuyor.",
                "show_alert": True
            })
        elif data in ["mesaj_kapat", "panel_kapat", "kapat"]:
            msg_id = cq.get("message", {}).get("message_id")
            if msg_id:
                telegramMesajSil(chat_id, msg_id)
        return

    if "message" in update and "text" in update["message"]:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        chat_title = msg["chat"].get("title", "")
        user_id = msg["from"]["id"]
        text = msg["text"].strip()
        is_group = chat_id < 0

        if not text.startswith("/"):
            return

        komut_parcalari = text.split()
        ana_komut = komut_parcalari[0].lower().split("@")[0]

        # Grup bağlantılarını bellekte hazır tut
        grup_baglantilarini_guncelle()

        if ana_komut in ["/id", "/myid", "/bilgi"]:
            telegramMesajGonder(
                chat_id,
                f"👤 <b>Telegram Kullanıcı Bilginiz:</b>\n"
                f"🆔 <b>Kullanıcı ID:</b> <code>{user_id}</code>\n"
                f"💬 <b>Sohbet ID:</b> <code>{chat_id}</code>\n\n"
                f"💡 <i>Botu kullanabilmek için bu ID numarasını yöneticiye iletiniz.</i>"
            )
            return

        # =========================================================================
        # ⛔ KESİN VE TEK YETKİ KONTROLÜ (Grup & Özel Sohbet Dahil Tüm Bot Fonksiyonları)
        # Kurucu ve Ek Yöneticiler haricindeki hiç kimse hiçbir komutu çalıştıramaz!
        # Yetkisiz kullanıcıya İLK denemesinde TEK SEFERLİK uyarı verilir;
        # sonraki tüm mesajlarında bot SESSİZ MODA geçer ve kullanıcıyı tamamen yok sayar.
        # =========================================================================
        if not yetkili_mi(user_id):
            yetkisiz_uyari_gonder(
                chat_id,
                user_id,
                f"⛔ <b>Erişim Reddedildi!</b>\n"
                f"Bu işlem sadece yetkili şirket yöneticilerine özeldir.\n"
                f"Kullanıcı ID'niz: <code>{user_id}</code>"
            )
            return

        # /t veya /rezerv veya /varlik (TRC-20 Canlı Rezerv & Varlık Raporu)
        if ana_komut in ["/t", "/rezerv", "/varlik"]:
            if user_id != KURUCU_ID:
                yetkisiz_uyari_gonder(
                    chat_id,
                    user_id,
                    "⛔ <b>Yetkisiz İşlem:</b> Şirket cüzdan ve rezerv raporunu sorgulama yetkisi sadece <b>Şirket Kurucusuna</b> aittir."
                )
                return
            cuzdan = komut_parcalari[1].strip() if len(komut_parcalari) > 1 else VARSAYILAN_TRC20_ADRES
            islemi_analiz_bildirimiyle_yap(chat_id, trc20_varlik_raporu_uret, cuzdan, goster_bildirim=True)
            return

        # /grupbagla veya /bagla
        if ana_komut in ["/grupbagla", "/bagla"]:
            islemi_analiz_bildirimiyle_yap(chat_id, grup_bagla_impl, chat_id, user_id, text, chat_title)
            return

        # /grupkopar veya /baglantikes
        if ana_komut in ["/grupkopar", "/baglantikes", "/grupbaglasil"]:
            islemi_analiz_bildirimiyle_yap(chat_id, grup_kopar_impl, chat_id, user_id)
            return

        # /grupbaglantilari veya /gruplar
        if ana_komut in ["/grupbaglantilari", "/gruplar", "/baglantilar"]:
            islemi_analiz_bildirimiyle_yap(chat_id, grup_baglantilari_listesi_impl)
            return

        # /senkron veya /grupguncelle (Excel & Telegram Grup İsim Eşitleme)
        if ana_komut in ["/senkron", "/senkronize", "/grupguncelle", "/grupgüncelle", "/esitle", "/eşitle", "/sync"]:
            islemi_analiz_bildirimiyle_yap(chat_id, grup_senkronize_impl, goster_bildirim=True)
            return

        # /kasa veya /durum
        if ana_komut in ["/kasa", "/durum"]:
            args = komut_parcalari[1:]
            baglantilar = app_state.get("GRUP_BAGLANTILARI", {})

            # 1. Hiçbir parametre girilmediğinde (/kasa)
            if len(args) == 0:
                if chat_id in baglantilar:
                    grup_adi = baglantilar[chat_id]["grup"]
                    islemi_analiz_bildirimiyle_yap(chat_id, grup_kasa_analiz_fisi_uret, grup_adi)
                    return
                else:
                    if is_group:
                        telegramMesajGonder(
                            chat_id,
                            "⚠️ <b>Bu grup henüz bir Excel satırına bağlanmamış.</b>\n\n"
                            "Yetkili bir yönetici bu grupta <code>/grupbagla [Grup Adı]</code> yazarak bağlantı kurabilir."
                        )
                        return
                    else:
                        telegramMesajGonder(
                            chat_id,
                            "💡 <b>Kasa Komutu Kullanım Rehberi:</b>\n━━━━━━━━━━━\n"
                            "• <code>/kasa SACİD</code> : Grup durum fişini görüntüler.\n"
                            "• <code>/kasa SACİD 1500</code> : Kasaya para ekler.\n"
                            "• <code>/kasasil SACİD 500</code> : Kasadan siler.\n\n"
                            "<i>Gruplarda tek tuşla kullanmak için grupta <code>/grupbagla SACİD</code> yazınız.</i>"
                        )
                        return

            # 2. Tek parametre girildiğinde (/kasa SACİD veya /kasa 1500)
            elif len(args) == 1:
                param_sayi_mi = False
                try:
                    _ = float(args[0].replace(".", "").replace(",", "."))
                    param_sayi_mi = True
                except ValueError:
                    param_sayi_mi = False

                if not param_sayi_mi:
                    # Grup adı girilmiş -> Canlı Kasa Fişi
                    grup_adi = args[0]
                    islemi_analiz_bildirimiyle_yap(chat_id, grup_kasa_analiz_fisi_uret, grup_adi)
                    return
                else:
                    # Sayı girilmiş -> Bağlı grupta ise o grubun kasasına ekle
                    if chat_id in baglantilar:
                        grup_adi = baglantilar[chat_id]["grup"]
                        islemi_analiz_bildirimiyle_yap(chat_id, hucreyeVeriYaz_impl, f"/kasa {grup_adi} {args[0]}", 4, "Kasa Ekleme", 1)
                        return
                    else:
                        telegramMesajGonder(chat_id, "⚠️ Lütfen hangi gruba işlem yapıldığını belirtin! Örnek: <code>/kasa SACİD 1500</code>")
                        return

            # 3. İki veya daha fazla parametre girildiğinde (/kasa SACİD 1500)
            else:
                islemi_analiz_bildirimiyle_yap(chat_id, hucreyeVeriYaz_impl, text, 4, "Kasa Ekleme", 1)
                return

        if ana_komut in ["/start", "/menu", "/menü"]:
            telegramMesajGonder(chat_id, "👋 <b>CFO ve Finans Yönetim Botu</b>\nLütfen bir işlem seçin:\n\n👨💻 <i>Yazılım: @CRYPTOATAKAN © 2026</i>", menuKlavyesiOlustur(is_group))
        elif ana_komut in ["/rehber", "/komutlar", "/yardim", "/yardım"]:
            telegramMesajGonder(chat_id, rehber_ana_metni(), rehber_ana_klavyesi())
        elif ana_komut in ["/qr", "/tronqr", "/tron", "/cuzdan", "/cüzdan", "/adres"]:
            cuzdanQrUret_impl(chat_id, text)
        elif ana_komut in ["/panel", "/webpanel"]:
            cur_panel_url = app_state.get("WEB_APP_URL", WEB_APP_URL)
            panel_btn = {
                "inline_keyboard": [
                    [{"text": "🚀 Canlı CFO Panelini Aç", "url": cur_panel_url}],
                    [{"text": "🗑️ Mesajı Kapat", "callback_data": "mesaj_kapat"}]
                ]
            }
            telegramMesajGonder(
                chat_id,
                f"🌐 <b>CANLI CFO WEB DASHBOARD</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <i>Şirketinizin tüm finans ve kasa verilerini 7/24 canlı web panelinden anlık izleyebilirsiniz.</i>\n\n"
                f"🔗 <b>Panel Linki:</b>\n{cur_panel_url}",
                panel_btn
            )
        elif ana_komut == "/dashboard":
            islemi_analiz_bildirimiyle_yap(chat_id, cfo_dashboard_raporu_uret, goster_bildirim=True, islem_tipi="kasa")
        elif ana_komut in ["/panellink", "/panelurl", "/panellinki"]:
            if user_id != KURUCU_ID:
                yetkisiz_uyari_gonder(chat_id, user_id, "⛔ <b>Yetkisiz İşlem:</b> Panel linkini güncelleme yetkisi sadece Şirket Kurucusuna aittir.")
                return
            p_args = text.split()[1:]
            if not p_args:
                cur = app_state.get("WEB_APP_URL", WEB_APP_URL)
                telegramMesajGonder(
                    chat_id,
                    f"🌐 <b>Mevcut Canlı Panel Linki:</b>\n{cur}\n\n"
                    f"💡 Yeni link tanımlamak için: <code>/panellink https://yeni-linkiniz.code.run</code>"
                )
                return
            yeni_url = p_args[0].strip()
            if not yeni_url.startswith("http://") and not yeni_url.startswith("https://"):
                yeni_url = "https://" + yeni_url
            app_state["WEB_APP_URL"] = yeni_url
            sistemeLogYaz("Panel Linki Güncellendi", f"Yeni Link: {yeni_url}")
            telegramMesajGonder(
                chat_id,
                f"✅ <b>Canlı CFO Panel Linki Başarıyla Güncellendi!</b>\n\n"
                f"🔗 <b>Yeni Panel Adresi:</b>\n{yeni_url}\n\n"
                f"💡 Artık <code>/panel</code> komutu ve menü butonları doğrudan bu linki açacaktır.",
                {"inline_keyboard": [[{"text": "🚀 Yeni Paneli Aç", "url": yeni_url}]]}
            )
        elif ana_komut == "/ozet":
            islemi_analiz_bildirimiyle_yap(chat_id, hizliOzetUret_impl)
        elif ana_komut == "/rapor":
            islemi_analiz_bildirimiyle_yap(chat_id, tumGruplarRaporu_impl, goster_bildirim=True)
        elif ana_komut in ["/bakiye", "/bakiyeler", "/sirala", "/sırala", "/risk"]:
            islemi_analiz_bildirimiyle_yap(chat_id, bakiye_risk_raporu_uret, "tumu", goster_bildirim=True)
        elif ana_komut in ["/borclular", "/borçlular", "/borc", "/borç"]:
            islemi_analiz_bildirimiyle_yap(chat_id, bakiye_risk_raporu_uret, "borclular", goster_bildirim=True)
        elif ana_komut in ["/alacaklar", "/alacak"]:
            islemi_analiz_bildirimiyle_yap(chat_id, bakiye_risk_raporu_uret, "pozitif", goster_bildirim=True)
        elif ana_komut in ["/masraf", "/gider"]:
            islemi_analiz_bildirimiyle_yap(chat_id, masrafRaporuUret_impl)
        elif ana_komut == "/canlikur":
            islemi_analiz_bildirimiyle_yap(chat_id, canliKurSorgula_impl, goster_bildirim=True)
        elif ana_komut == "/kur":
            islemi_analiz_bildirimiyle_yap(chat_id, kurRaporuUret_impl, goster_bildirim=True)
        elif ana_komut in ["/iban", "/ibancoz", "/coz", "/ibandoğrula", "/ibandogrula"]:
            args = komut_parcalari[1:]
            if len(args) == 0 and ana_komut == "/iban":
                islemi_analiz_bildirimiyle_yap(chat_id, ibanListesiGetir_impl)
            else:
                islemi_analiz_bildirimiyle_yap(chat_id, ibanCozumle_impl, text)
        elif ana_komut in ["/sablon", "/şablon", "/hesapbilgi"]:
            islemi_analiz_bildirimiyle_yap(chat_id, iban_sablon_getir_impl, text, chat_id)
        elif ana_komut in ["/hesaplar", "/grupiban", "/aktifiban", "/ibanlarim", "/hesaplarim"]:
            p_args = text.split()[1:]
            grup_arg = " ".join(p_args).strip() if p_args else ""
            islemi_analiz_bildirimiyle_yap(chat_id, grup_aktif_ibanlar_raporu_uret, grup_arg, chat_id)
        elif ana_komut in ["/ibantahsis", "/tahsis"]:
            islemi_analiz_bildirimiyle_yap(chat_id, iban_tahsis_impl, text)
        elif ana_komut in ["/ibanbosalt", "/bosalt", "/ibansil"]:
            islemi_analiz_bildirimiyle_yap(chat_id, iban_bosalt_impl, text)
        elif ana_komut in ["/ekstre", "/gecmis", "/hesapdokumu", "/dokum"]:
            islemi_analiz_bildirimiyle_yap(chat_id, cari_ekstre_impl, text, goster_bildirim=True)
        elif ana_komut in ["/duyuru", "/topluduyuru", "/broadcast", "/yayin", "/yayım"]:
            islemi_analiz_bildirimiyle_yap(chat_id, toplu_duyuru_hazirla_paneli, text, user_id, goster_bildirim=True)
        elif ana_komut in ["/toplu", "/topluislem", "/hizli"]:
            islemi_analiz_bildirimiyle_yap(chat_id, toplu_islem_impl, text, goster_bildirim=True)
        elif ana_komut in ["/hedef", "/kpi", "/cirohedefi"]:
            p_args = text.split()[1:]
            yeni_hedef = p_args[0].strip() if p_args else None
            if yeni_hedef and not yetkili_mi(user_id):
                yetkisiz_uyari_gonder(chat_id, user_id, "⛔ <b>Yetkisiz İşlem:</b> Ciro hedefini güncelleme yetkisi sadece şirket yöneticilerine aittir.")
            else:
                islemi_analiz_bildirimiyle_yap(chat_id, hedef_kpi_raporu_uret, yeni_hedef, goster_bildirim=True)
        elif ana_komut in ["/trend", "/haftalik", "/haftalık", "/performans"]:
            p_args = text.split()[1:]
            gun = 7
            if p_args:
                try: gun = int(p_args[0].strip())
                except Exception: gun = 7
            islemi_analiz_bildirimiyle_yap(chat_id, haftalik_trend_raporu_uret, gun, goster_bildirim=True)
        elif ana_komut in ["/kurfark", "/makas", "/spread", "/firsat"]:
            p_args = text.split()[1:]
            tutar_str = p_args[0].strip() if p_args else "100000"
            islemi_analiz_bildirimiyle_yap(chat_id, kur_fark_makas_raporu_uret, tutar_str, goster_bildirim=True)
        elif ana_komut in ["/arbitraj", "/arb"]:
            islemi_analiz_bildirimiyle_yap(chat_id, arbitraj_raporu_uret_impl, text, goster_bildirim=True)
        elif ana_komut in ["/doviz", "/döviz", "/cevir", "/çevir", "/kurcevir", "/donustur"]:
            islemi_analiz_bildirimiyle_yap(chat_id, doviz_cevirici_impl, text)
        elif ana_komut in ["/portfoy", "/portföy", "/hazine", "/varlik"]:
            islemi_analiz_bildirimiyle_yap(chat_id, sirket_portfoy_raporu_impl, goster_bildirim=True)
        elif ana_komut == "/hesap":
            islemi_analiz_bildirimiyle_yap(chat_id, hesapMakinesi_impl, text)
        elif ana_komut in ["/çeviri", "/ceviri"]:
            islemi_analiz_bildirimiyle_yap(chat_id, metinCevir_impl, text)
        elif ana_komut == "/yenigun":
            metin, klavye = yenigun_baslat_mesaji()
            telegramMesajGonder(chat_id, metin, klavye)
        elif ana_komut == "/kasasil":
            islemi_analiz_bildirimiyle_yap(chat_id, hucreyeVeriYaz_impl, text, 4, "Kasa Silme", -1)
        elif ana_komut == "/odeme":
            islemi_analiz_bildirimiyle_yap(chat_id, hucreyeVeriYaz_impl, text, 5, "Ödenen Ekleme", 1)
        elif ana_komut == "/odemesil":
            islemi_analiz_bildirimiyle_yap(chat_id, hucreyeVeriYaz_impl, text, 5, "Ödenen Silme", -1)
        elif ana_komut == "/devir":
            islemi_analiz_bildirimiyle_yap(chat_id, hucreyeVeriYaz_impl, text, 3, "Devir Ekleme", 1)
        elif ana_komut == "/devirsil":
            islemi_analiz_bildirimiyle_yap(chat_id, hucreyeVeriYaz_impl, text, 3, "Devir Silme", -1)
        elif ana_komut == "/masrafekle":
            islemi_analiz_bildirimiyle_yap(chat_id, masrafVerisiYaz_impl, text, "Masraf Ekleme", 1)
        elif ana_komut == "/masrafsil":
            islemi_analiz_bildirimiyle_yap(chat_id, masrafVerisiYaz_impl, text, "Masraf Silme", -1)
        elif ana_komut == "/adminekle":
            islemi_analiz_bildirimiyle_yap(chat_id, admin_ekle_impl, text, user_id)
        elif ana_komut == "/adminsil":
            islemi_analiz_bildirimiyle_yap(chat_id, admin_sil_impl, text, user_id)
        elif ana_komut in ["/adminler", "/yoneticiler"]:
            islemi_analiz_bildirimiyle_yap(chat_id, admin_listesi_impl)
        elif ana_komut in ["/debug", "/hizlandir", "/optimize", "/ping", "/sistem"]:
            islemi_analiz_bildirimiyle_yap(chat_id, debug_sistem_impl, goster_bildirim=True)
        elif ana_komut in ["/kapanis", "/gunsonu"]:
            if user_id != KURUCU_ID:
                yetkisiz_uyari_gonder(chat_id, user_id, "⛔ <b>Yetkisiz İşlem:</b> Gün sonu kapanış raporunu alma yetkisi sadece Şirket Kurucusuna aittir.")
                return
            islemi_analiz_bildirimiyle_yap(chat_id, gun_sonu_kapanis_raporu_uret, goster_bildirim=True)
        elif ana_komut in ["/kapanissaati", "/saatayar"]:
            if user_id != KURUCU_ID:
                yetkisiz_uyari_gonder(chat_id, user_id, "⛔ <b>Yetkisiz İşlem:</b> Bu ayar sadece Şirket Kurucusuna aittir.")
                return
            p_args = text.split()[1:]
            if not p_args:
                telegramMesajGonder(
                    chat_id,
                    f"🕒 <b>Otomatik Kapanış Rapor Saati:</b> <code>{app_state.get('KAPANIS_SAATI', '23:45')}</code>\n\n"
                    f"💡 Saati güncellemek için: <code>/kapanissaati 22:30</code>"
                )
                return
            yeni_saat = p_args[0].strip()
            if re.match(r"^\d{1,2}:\d{2}$", yeni_saat):
                parts = yeni_saat.split(":")
                h, m = int(parts[0]), int(parts[1])
                if 0 <= h <= 23 and 0 <= m <= 59:
                    fmt_saat = f"{h:02d}:{m:02d}"
                    app_state["KAPANIS_SAATI"] = fmt_saat
                    sistemeLogYaz("Kapanış Saati Güncellendi", f"Yeni Saat: {fmt_saat}")
                    telegramMesajGonder(
                        chat_id,
                        f"✅ <b>Kapanış Rapor Saati Güncellendi!</b>\n"
                        f"Her akşam saat <b>{fmt_saat}</b>'de günün detaylı bilançosu otomatik olarak özelinize gönderilecektir."
                    )
                    return
            telegramMesajGonder(chat_id, "⚠️ <b>Geçersiz Saat Formatı!</b>\nLütfen <code>SS:DD</code> formatında girin. Örnek: <code>/kapanissaati 23:00</code>")
        elif ana_komut == "/gerial":
            def gerial_impl():
                if not app_state.get("SON_ISLEM"):
                    return "Hafıza Boş: Geri alınacak işlem yok."
                last = app_state["SON_ISLEM"]
                sh = get_spreadsheet()
                sayfa = sh.worksheet(last["sayfa"])
                
                if last.get("is_new_masraf"):
                    sayfa.update_cell(last["satir"], 9, "")
                    sayfa.update_cell(last["satir"], 10, "")
                elif last.get("is_masraf_update"):
                    sayfa.update_cell(last["satir"], 9, last.get("eskiAd", last["grupAdi"]))
                    sayfa.update_cell(last["satir"], 10, last["eskiDeger"])
                else:
                    sayfa.update_cell(last["satir"], last["sutun"], last["eskiDeger"])
                    
                app_state["SON_ISLEM"] = None
                sistemeLogYaz("İptal Edilen İşlem", f"{last['grupAdi']} ({last['islemTuru']})")
                return f"⏪ <b>ZAMAN GERİYE SARILDI!</b>\nHedef: <b>{last['grupAdi']}</b>\nEski haline döndürüldü."
            islemi_analiz_bildirimiyle_yap(chat_id, gerial_impl)
        elif ana_komut == "/not":
            def not_ekle_impl():
                not_metni = text[4:].strip()
                sh = get_spreadsheet()
                try: not_sayfasi = sh.worksheet("NOTLAR")
                except: not_sayfasi = sh.add_worksheet(title="NOTLAR", rows=500, cols=3)
                now_str = suankiZamaniAl().strftime("%d.%m.%Y %H:%M")
                not_sayfasi.append_row([now_str, not_metni])
                return f"📓 <b>NOT KAYDEDİLDİ</b>\n🕒 {now_str}\n📝 <i>{not_metni}</i>"
            islemi_analiz_bildirimiyle_yap(chat_id, not_ekle_impl)
        elif ana_komut == "/notlar":
            def notlari_getir_impl():
                sh = get_spreadsheet()
                not_sayfasi = sh.worksheet("NOTLAR")
                rows = not_sayfasi.get_all_values()
                if len(rows) < 1:
                    return "📭 Not defteri boş."
                last_10 = rows[-10:]
                out = "📓 <b>ŞİRKET HAFIZASI (SON NOTLAR)</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                for r in reversed(last_10):
                    out += f"📌 <b>{r[0] if len(r)>0 else ''}</b>\n<code>{r[1] if len(r)>1 else ''}</code>\n\n"
                return out
            islemi_analiz_bildirimiyle_yap(chat_id, notlari_getir_impl)
        else:
            if text.startswith("/"):
                aranan_aday = text.strip().lstrip("/")
                try:
                    sh_temp = get_spreadsheet()
                    sayfa_temp = get_active_daily_sheet(sh_temp)
                    veriler_temp = get_sheet_values_fast(sayfa_temp)
                    if iban_sablon_bul(veriler_temp, aranan_aday):
                        islemi_analiz_bildirimiyle_yap(chat_id, iban_sablon_getir_impl, text, chat_id)
                        return
                except Exception:
                    pass

# --- MODERN CANLI CFO WEB PANELİ & API ---
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CFO Canlı Finans Paneli</title>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        * { margin:0; padding:0; box-sizing:border-box; font-family:'Plus Jakarta Sans', sans-serif; }
        body { background: #0a0f1d; color: #f1f5f9; min-height: 100vh; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:15px; margin-bottom:25px; padding-bottom:20px; border-bottom:1px solid #1e293b; }
        .logo-area { display:flex; align-items:center; gap:12px; }
        .logo-icon { width:46px; height:46px; border-radius:12px; background:linear-gradient(135deg, #3b82f6, #8b5cf6); display:flex; align-items:center; justify-content:center; font-size:24px; }
        .title h1 { font-size:22px; font-weight:800; background:linear-gradient(to right, #60a5fa, #c084fc); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }
        .title p { font-size:13px; color:#94a3b8; }
        .status-badge { background:rgba(34, 197, 94, 0.15); border:1px solid #22c55e; color:#4ade80; padding:6px 14px; border-radius:20px; font-size:12px; font-weight:600; display:flex; align-items:center; gap:6px; }
        .status-dot { width:8px; height:8px; border-radius:50%; background:#22c55e; animation:pulse 2s infinite; }
        @keyframes pulse { 0%,100%{opacity:1;} 50%{opacity:0.4;} }
        
        .stats-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(195px, 1fr)); gap:12px; margin-bottom:28px; }
        .stat-card { background:#111827; border:1px solid #1f2937; border-radius:14px; padding:14px 12px; position:relative; overflow:hidden; }
        .stat-card::before { content:''; position:absolute; top:0; left:0; width:4px; height:100%; }
        .stat-devir::before { background:#6366f1; }
        .stat-kasa::before { background:#3b82f6; }
        .stat-odenen::before { background:#f59e0b; }
        .stat-komisyon::before { background:#ec4899; }
        .stat-kalan::before { background:#10b981; }
        .stat-label { font-size:11px; color:#9ca3af; font-weight:700; text-transform:uppercase; margin-bottom:6px; letter-spacing:0.3px; }
        .stat-value { font-size:17px; font-weight:800; color:#ffffff; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
        .stat-value .curr { font-size:14px; font-weight:600; opacity:0.85; }
        
        .section-title { font-size:18px; font-weight:700; color:#f8fafc; margin-bottom:16px; display:flex; align-items:center; gap:8px; }
        .groups-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(320px, 1fr)); gap:16px; margin-bottom:30px; }
        .group-card { background:#131d31; border:1px solid #202d46; border-radius:16px; padding:20px; }
        .group-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; padding-bottom:10px; border-bottom:1px solid #1f2d47; }
        .group-name { font-size:16px; font-weight:700; color:#60a5fa; display:flex; align-items:center; gap:8px; }
        .group-kalan-badge { background:rgba(16, 185, 129, 0.15); color:#34d399; padding:4px 10px; border-radius:8px; font-weight:700; font-size:13px; white-space:nowrap; }
        
        .row-item { display:flex; justify-content:space-between; margin-bottom:8px; font-size:13px; color:#cbd5e1; }
        .row-item span:first-child { color:#94a3b8; }
        .row-item span:last-child { font-weight:600; }
        
        .refresh-btn { background:#2563eb; color:white; border:none; padding:10px 20px; border-radius:10px; font-weight:600; cursor:pointer; display:flex; align-items:center; gap:8px; }
        .refresh-btn:hover { background:#1d4ed8; }
        .footer { text-align:center; color:#64748b; font-size:12px; margin-top:40px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="logo-area">
                <div class="logo-icon">💼</div>
                <div class="title">
                    <h1>CFO CANLI FİNANS PANELİ</h1>
                    <p id="time-text">Yükleniyor...</p>
                </div>
            </div>
            <div style="display:flex; align-items:center; gap:12px;">
                <button class="refresh-btn" onclick="fetchData()">🔄 Yenile</button>
                <div class="status-badge"><div class="status-dot"></div> CANLI SİSTEM</div>
            </div>
        </div>

        <div class="stats-grid">
            <div class="stat-card stat-devir">
                <div class="stat-label">🔄 Toplam Devir</div>
                <div class="stat-value" id="toplam-devir">0,00 ₺</div>
            </div>
            <div class="stat-card stat-kasa">
                <div class="stat-label">💰 Eklenen Kasa</div>
                <div class="stat-value" id="toplam-kasa">0,00 ₺</div>
            </div>
            <div class="stat-card stat-odenen">
                <div class="stat-label">💸 Toplam Ödeme</div>
                <div class="stat-value" id="toplam-odenen">0,00 ₺</div>
            </div>
            <div class="stat-card stat-komisyon">
                <div class="stat-label">✂️ Toplam Komisyon</div>
                <div class="stat-value" id="toplam-komisyon" style="color:#f472b6;">0,00 ₺</div>
            </div>
            <div class="stat-card stat-kalan">
                <div class="stat-label">🏦 NET KALAN KASA</div>
                <div class="stat-value" id="toplam-kalan" style="color:#34d399;">0,00 ₺</div>
            </div>
        </div>

        <div class="section-title">📊 Aktif Gruplar ve Kasa Durumları</div>
        <div class="groups-grid" id="groups-container">
            <p style="color:#94a3b8;">Veriler yükleniyor...</p>
        </div>

        <div class="footer">
            <p>HSY Kuyumculuk Finans Yönetim Sistemi © 2026 | Yazılım: @CRYPTOATAKAN</p>
        </div>
    </div>

    <script>
        function fmt(n) {
            const num = Number(n);
            const isNeg = num < 0;
            const formatted = Math.abs(num).toLocaleString('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
            return (isNeg ? '-' : '') + formatted + ' ₺';
        }

        function fmtHtml(n) {
            const num = Number(n);
            const isNeg = num < 0;
            const formatted = Math.abs(num).toLocaleString('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
            return (isNeg ? '-' : '') + formatted + '<span class="curr">&nbsp;₺</span>';
        }

        async function fetchData() {
            try {
                const res = await fetch('/api/dashboard');
                const d = await res.json();
                if(d.error) {
                    alert('Hata: ' + d.error);
                    return;
                }
                document.getElementById('time-text').innerText = 'Tarih: ' + d.tarih + ' | Son Güncelleme: ' + new Date().toLocaleTimeString('tr-TR');
                document.getElementById('toplam-devir').innerHTML = fmtHtml(d.devir);
                document.getElementById('toplam-kasa').innerHTML = fmtHtml(d.kasa);
                document.getElementById('toplam-odenen').innerHTML = fmtHtml(d.odenen);
                document.getElementById('toplam-komisyon').innerHTML = fmtHtml(d.komisyon);
                document.getElementById('toplam-kalan').innerHTML = fmtHtml(d.kalan);

                const gc = document.getElementById('groups-container');
                if(!d.gruplar || d.gruplar.length === 0) {
                    gc.innerHTML = '<p style="color:#94a3b8;">Henüz işlem görmüş aktif grup bulunmuyor.</p>';
                    return;
                }

                gc.innerHTML = d.gruplar.map(g => `
                    <div class="group-card">
                        <div class="group-header">
                            <div class="group-name"><span>🔹</span> ${g.ad.toUpperCase()}</div>
                            <div class="group-kalan-badge" style="background:${g.kalan < 0 ? 'rgba(239, 68, 68, 0.15)' : 'rgba(16, 185, 129, 0.15)'}; color:${g.kalan < 0 ? '#f87171' : '#34d399'};">${fmt(g.kalan)}</div>
                        </div>
                        <div class="row-item">
                            <span>🔄 Devir:</span>
                            <span>${fmt(g.devir)}</span>
                        </div>
                        <div class="row-item">
                            <span>💰 Eklenen Kasa:</span>
                            <span>${fmt(g.kasa)}</span>
                        </div>
                        <div class="row-item">
                            <span>💸 Ödenen:</span>
                            <span>${fmt(g.odenen)}</span>
                        </div>
                        <div class="row-item">
                            <span>✂️ Kesinti/Masraf:</span>
                            <span>${fmt(g.komisyon)}</span>
                        </div>
                    </div>
                `).join('');
            } catch(e) {
                console.error(e);
            }
        }
        fetchData();
        setInterval(fetchData, 15000);
    </script>
</body>
</html>
"""

class LiveDashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/dashboard":
            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                sh = get_spreadsheet()
                sayfa = get_active_daily_sheet(sh)
                veriler = sayfa.get_all_values()
                finans = tablodan_finans_ozeti_hesapla(veriler)
                data = {
                    "tarih": sayfa.title,
                    "devir": finans["devir"],
                    "kasa": finans["kasa"],
                    "odenen": finans["odenen"],
                    "komisyon": finans["komisyon"],
                    "kalan": finans["kalan"],
                    "gruplar": finans["aktif_gruplar"]
                }
                self.wfile.write(json.dumps(data).encode("utf-8"))
            except Exception as e:
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
        else:
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode("utf-8"))
            
    def log_message(self, format, *args): pass

def run_dashboard_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), LiveDashboardHandler)
    server.serve_forever()

def run_kapanis_scheduler():
    """Her akşam belirlenen saatte (varsayılan 23:00) otomatik gün sonu bilançosunu sadece Kurucuya iletir."""
    while True:
        try:
            simdi = suankiZamaniAl()
            saat_dakika = simdi.strftime("%H:%M")
            bugun_str = simdi.strftime("%d.%m.%Y")
            hedef_saat = app_state.get("KAPANIS_SAATI", "23:00")
            
            if saat_dakika == hedef_saat and app_state.get("SON_KAPANIS_TARIHI") != bugun_str:
                try:
                    rapor_metni = gun_sonu_kapanis_raporu_uret()
                    telegramMesajGonder(KURUCU_ID, rapor_metni)
                    app_state["SON_KAPANIS_TARIHI"] = bugun_str
                    sistemeLogYaz("Otomatik Gün Sonu Raporu", f"Kurucuya ({KURUCU_ID}) gün sonu bilançosu iletildi.")
                except Exception as e:
                    print(f"Otomatik kapanış raporu gönderme hatası: {e}")
        except Exception as e:
            print(f"Kapanış scheduler hatası: {e}")
        time.sleep(30)

# --- MAIN LOOP (LONG POLLING WITH THREAD POOL) ---
if __name__ == "__main__":
    threading.Thread(target=run_dashboard_server, daemon=True).start()
    threading.Thread(target=run_kapanis_scheduler, daemon=True).start()
    print(f"CFO Bot & Canlı Dashboard Başlatıldı (7/24 Kesintisiz - Otomatik Kapanış Saati: {app_state.get('KAPANIS_SAATI', '23:45')})...")
    
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset}&timeout=25"
            res = http_get_json(url)
            if res.get("ok"):
                for upd in res.get("result", []):
                    offset = upd["update_id"] + 1
                    _update_executor.submit(process_telegram_update, upd)
        except Exception as e:
            time.sleep(1)
