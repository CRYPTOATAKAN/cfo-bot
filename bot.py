import os
import re
import io
import json
import time
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
WEB_APP_URL = os.environ.get("WEB_APP_URL", "https://site--cfo-bot-servis--drx8qyvbw8cw.code.run")
LOG_SAYFASI = "Guvenlik_Log"
ADMIN_SAYFASI = "YONETICILER"
BAGLANTI_SAYFASI = "GRUP_BAGLANTILARI"
VARSAYILAN_TRC20_ADRES = os.environ.get("TRC20_WALLET_ADDRESS", "TQHuwJh5c4ygbKhfFoGqTZTahjQuJAX3iV")

# --- PARALEL İŞ PARÇACIĞI HAVUZLARI (YÜKSEK PERFORMANS) ---
_update_executor = concurrent.futures.ThreadPoolExecutor(max_workers=16, thread_name_prefix="UpdateWorker")
_log_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="LogWorker")

app_state = {
    "EK_ADMINLER": set(),
    "GRUP_BAGLANTILARI": {},
    "BAGLANTI_CACHE_TIME": 0,
    "SISTEM_KILIDI": "PASIF",
    "CIRO_HEDEFI": None,
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
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))

def telegram_api(method: str, payload: dict) -> dict:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as e:
        print(f"Telegram API Hatası ({method}): {e}")
        return {"ok": False, "error": str(e)}

def telegramMesajGonder(chat_id, metin: str, reply_markup=None):
    payload = {"chat_id": chat_id, "text": metin, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return telegram_api("sendMessage", payload)

def telegramFotoGonder(chat_id, foto_url: str, caption: str = None, reply_markup=None):
    payload = {"chat_id": chat_id, "photo": foto_url, "parse_mode": "HTML"}
    if caption:
        payload["caption"] = caption
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return telegram_api("sendPhoto", payload)

def telegramMesajSil(chat_id, message_id):
    return telegram_api("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

def telegramMesajDuzenle(chat_id, message_id, metin: str, reply_markup=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": metin, "parse_mode": "HTML"}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return telegram_api("editMessageText", payload)

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
    except:
        return False

def get_active_daily_sheet(sh) -> gspread.Worksheet:
    """Excel tablosundaki en son tarihli aktif çalışma sayfasını bulur."""
    tum_ws = sh.worksheets()
    tarih_sayfalari = []
    
    for ws in tum_ws:
        if is_valid_daily_sheet(ws) and re.match(r'^\d{2}\.\d{2}\.\d{4}$', ws.title):
            try:
                t_obj = datetime.datetime.strptime(ws.title, "%d.%m.%Y")
                tarih_sayfalari.append((t_obj, ws))
            except: pass
            
    if tarih_sayfalari:
        tarih_sayfalari.sort(key=lambda x: x[0], reverse=True)
        return tarih_sayfalari[0][1]
        
    for ws in tum_ws:
        if is_valid_daily_sheet(ws):
            return ws
            
    return tum_ws[0]

def bugununTarihiniAl() -> str:
    """Aktif en son sayfanın adını döner."""
    try:
        sh = get_spreadsheet()
        ws = get_active_daily_sheet(sh)
        return ws.title
    except:
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
    temiz = normalize_text(grupAdi)
    if "TIGER" in temiz: return "🐅"
    if "SACID" in temiz: return "👤"
    if "BSM" in temiz: return "⚡"
    if "GENELTOPLAM" in temiz: return "🏆"
    if "MASRAF" in temiz or "GIDER" in temiz: return "📉"
    if "KARGO" in temiz: return "📦"
    return "🔹"

def rakamFormatla(sayi) -> str:
    try:
        val = int(round(float(sayi)))
        is_neg = val < 0
        val_str = f"{abs(val):,}".replace(",", ".")
        return f"-{val_str}" if is_neg else val_str
    except:
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
    except:
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
    except:
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
    out = "🔗 <b>BAĞLI TELEGRAM GRUPLARI</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for c_id, info in baglantilar.items():
        g_ad = info.get("grup", "Bilinmiyor")
        title = info.get("title", "")
        title_str = f" ({title})" if title else ""
        out += f"🏢 <b>Excel Cari:</b> <code>{g_ad}</code>\n💬 <b>Grup ID:</b> <code>{c_id}</code>{title_str}\n\n"
    return out

def grup_kasa_analiz_fisi_uret(grup_ham: str) -> str:
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

    return (
        f"📊 <b>[ {gercek_grup_adi.upper()} ] GÜNCEL KASA ANALİZİ</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📅 Tarih: {tarih_str} | ⏰ Saat: {saat_str}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔄 Önceki Devir: {paraFormatla(devir)}\n"
        f"💰 Eklenen Kasa: {paraFormatla(kasa)}\n"
        f"💸 Yapılan Ödeme: {paraFormatla(odenen)}\n"
        f"✂️ Kesinti/Masraf: {paraFormatla(komisyon)}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🏦 <b>NET KALAN TL: {paraFormatla(kalan)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━"
    )

def menuKlavyesiOlustur(isGroup: bool):
    panel_button = (
        {"text": "🌐 Canlı CFO Paneli (Tarayıcıda Aç)", "url": WEB_APP_URL}
        if (isGroup or not WEB_APP_URL) else
        {"text": "🌐 Canlı CFO Paneli (Mini-App)", "web_app": {"url": WEB_APP_URL}}
    )
    keyboard = [
        [panel_button],
        [{"text": "📊 Tüm Gruplar Raporu", "callback_data": "rapor_tumu"}],
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
    except:
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
            "• <code>/kasa</code>\n"
            "  └ <i>Bağlı Telegram grubunda tek tuşla canlı kasa durum fişini döker.</i>\n\n"
            "• <code>/kasa [Grup] [Tutar]</code>\n"
            "  └ <i>Kasaya nakit ekler.</i>\n"
            "  └ <i>Örnek:</i> <code>/kasa SACİD 500.000</code> veya <code>/kasa TİGER 1.250,50</code>\n\n"
            "• <code>/kasasil [Grup] [Tutar]</code>\n"
            "  └ <i>Kasa tutarından düşer.</i>\n"
            "  └ <i>Örnek:</i> <code>/kasasil SACİD 50.000</code>\n\n"
            "• <code>/odeme [Grup] [Tutar]</code>\n"
            "  └ <i>Yapılan ödemeyi işler.</i>\n"
            "  └ <i>Örnek:</i> <code>/odeme SACİD 100.000</code>\n\n"
            "• <code>/odemesil [Grup] [Tutar]</code>\n"
            "  └ <i>Ödenen tutardan düşer.</i>\n\n"
            "• <code>/devir [Grup] [Tutar]</code>\n"
            "  └ <i>Cari satırına devir / borç bakiyesi ekler.</i>\n"
            "  └ <i>Örnek:</i> <code>/devir TİGER 250.000</code>\n\n"
            "• <code>/devirsil [Grup] [Tutar]</code>\n"
            "  └ <i>Devir tutarından düşer.</i>\n\n"
            "• <code>/gerial</code>\n"
            "  └ <i>En son yapılan hatalı işlemi hafızadan geri alır.</i>"
        )
    elif kategori == "masraf":
        return (
            "📉 <b>MASRAF VE GİDER YÖNETİMİ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "• <code>/masrafekle [Kalem] [Tutar]</code>\n"
            "  └ <i>Excel'deki ilk boş satıra yeni masraf kalemi olarak işler.</i>\n"
            "  └ <i>Örnek:</i> <code>/masrafekle Yemek 1.250,50</code>\n"
            "  └ <i>Örnek:</i> <code>/masrafekle ABI 500.000</code>\n\n"
            "• <code>/masrafsil [Kalem] [Tutar]</code>\n"
            "  └ <i>İlgili masrafı son eklenen satırdan siler veya tutarını düşer.</i>\n"
            "  └ <i>Örnek:</i> <code>/masrafsil Yemek 250</code>\n\n"
            "• <code>/masraf</code> veya <code>/gider</code>\n"
            "  └ <i>Günün tüm masraf kalemlerini ve toplam gider bilançosunu listeler.</i>"
        )
    elif kategori == "grup":
        return (
            "👥 <b>GRUP VE CARİ EŞLEŞTİRME</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "• <code>/grupbagla [Grup Adı]</code>\n"
            "  └ <i>Bu Telegram grubunu Excel'deki cari satırına bağlar (Grupta bir kez çalıştırılır).</i>\n"
            "  └ <i>Örnek:</i> <code>/grupbagla SACİD</code>\n\n"
            "• <code>/grupkopar</code>\n"
            "  └ <i>İçinde bulunulan grubun Excel eşleştirmesini kaldırır.</i>\n\n"
            "• <code>/gruplar</code>\n"
            "  └ <i>Hangi Telegram grubunun hangi Excel carisine bağlı olduğunu listeler.</i>"
        )
    elif kategori == "rapor":
        return (
            "📊 <b>GÜNLÜK DÖNGÜ VE RAPORLAR</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "• <code>/ozet</code>\n"
            "  └ <i>Toplam devir, kasa, ödeme, komisyon ve net kalan anlık şirket bilançosu.</i>\n\n"
            "• <code>/rapor</code>\n"
            "  └ <i>Tüm aktif grupların ayrıntılı döküm raporunu verir.</i>\n\n"
            "• <code>/yenigun</code>\n"
            "  └ <i>🌅 Gün sonu devir işlemi: Dünün net kalan kasasını yeni günün devrine aktarır, güncel kasa ve ödemeleri sıfırlayarak yeni sayfa açar.</i>"
        )
    elif kategori == "kripto":
        return (
            "🪙 <b>KRİPTO, KUR VE FİNANS ARAÇLARI</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "• <code>/kur</code>\n"
            "  └ <i>Binance, Paribu, BtcTurk, WhiteBIT canlı USDT/TRY kurlarını listeler.</i>\n\n"
            "• <code>/canlikur</code>\n"
            "  └ <i>Dünya para birimleri (USD, EUR, GBP) ve global piyasa kurları.</i>\n\n"
            "• <code>/hesap [Grup] [Komisyon%] [Kur]</code>\n"
            "  └ <i>Tether / Komisyon hesap makinesi.</i>\n"
            "  └ <i>Örnek:</i> <code>/hesap SACİD 2 48.00</code>\n\n"
            "• <code>/iban</code>\n"
            "  └ <i>Kullanımdaki ve boşta olan şirket İBAN'larını listeler.</i>\n\n"
            "• <code>/ibantahsis [Hesap] [Cari]</code>\n"
            "  └ <i>İBAN'ı cariye tahsis edip 'Kullanımda' yapar (Örn: /ibantahsis CYL1 SACİD).</i>\n\n"
            "• <code>/ibanbosalt [Hesap]</code>\n"
            "  └ <i>İBAN'ı boşa çıkarır ve 'Müsait' yapar (Örn: /ibanbosalt CYL1).</i>\n\n"
            "• <code>/ibancoz [İBAN]</code>\n"
            "  └ <i>İBAN'ı doğrular (MOD-97), bankasını bulur ve temiz kopyalama formatı üretir.</i>\n\n"
            "• <code>/ekstre [Cari] [Gün]</code>\n"
            "  └ <i>Carinin son 5 günlük Devir, Kasa, Ödeme ve Kalan hesap ekstresini döker.</i>\n\n"
            "• <code>/t [Cüzdan Adresi]</code>\n"
            "  └ <i>🏛️ TRC-20 canlı blokzincir USDT rezervini ve TL karşılığını raporlar.</i>\n\n"
            "• <code>/qr [Cüzdan Adresi]</code>\n"
            "  └ <i>⚡ Hızlı ödeme QR kodu üretir ve borsa/istihbarat analizi yapar.</i>"
        )
    elif kategori == "admin":
        return (
            "🛡️ <b>YÖNETİCİ KONTROLLERİ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "• <code>/adminler</code>\n"
            "  └ <i>Sistemde yetkilendirilmiş şirket yöneticilerini listeler.</i>\n\n"
            "• <code>/adminekle [Telegram ID] [İsim]</code>\n"
            "  └ <i>Botu kullanabilmesi için yeni bir yönetici yetkilendirir (Sadece Kurucu).</i>\n\n"
            "• <code>/adminsil [Telegram ID]</code>\n"
            "  └ <i>Yöneticinin bot yetkisini geri alır.</i>\n\n"
            "• <code>/kapanis</code>\n"
            "  └ <i>🌙 Gün sonu kapanış bilançosunu anında özelinize gönderir (Sadece Kurucu).</i>\n\n"
            "• <code>/kapanissaati [SS:DD]</code>\n"
            "  └ <i>Otomatik gün sonu bildirim saatini ayarlar (Örn: /kapanissaati 23:00).</i>\n\n"
            "• <code>/panel</code>\n"
            "  └ <i>Canlı CFO Web Dashboard bağlantı linkini verir.</i>\n\n"
            "• <code>/id</code>\n"
            "  └ <i>Kendi Telegram kullanıcı ID numaranızı görüntüler.</i>"
        )
    else:  # "tumu"
        return (
            "📚 <b>TÜM SİSTEM KOMUTLARI</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "🏢 <b>KASA VE OPERASYON</b>\n"
            "• <code>/kasa</code> : Canlı durum fişi döker.\n"
            "• <code>/kasa [Grup] [Tutar]</code> : Kasaya nakit ekler.\n"
            "• <code>/kasasil [Grup] [Tutar]</code> : Kasadan tutar siler.\n"
            "• <code>/odeme [Grup] [Tutar]</code> : Ödenen tutarı işler.\n"
            "• <code>/odemesil [Grup] [Tutar]</code> : Ödenen tutardan düşer.\n"
            "• <code>/devir [Grup] [Tutar]</code> : Devir bakiyesi ekler.\n"
            "• <code>/devirsil [Grup] [Tutar]</code> : Devirden siler.\n"
            "• <code>/masrafekle [Kalem] [Tutar]</code> : Sonraki boş satıra masraf işler.\n"
            "• <code>/masrafsil [Kalem] [Tutar]</code> : Masraf siler/düşer.\n"
            "• <code>/masraf</code> : Günlük masraf listesini döker.\n"
            "• <code>/gerial</code> : En son işlemi geri alır.\n\n"
            "👥 <b>GRUP VE CARİ EŞLEŞTİRME</b>\n"
            "• <code>/grupbagla [Grup Adı]</code> : Grubu Excel satırına bağlar.\n"
            "• <code>/grupkopar</code> : Grubun Excel bağlantısını kaldırır.\n"
            "• <code>/gruplar</code> : Bağlı grupları listeler.\n\n"
            "📊 <b>GÜNLÜK DÖNGÜ VE RAPORLAR</b>\n"
            "• <code>/ozet</code> : Kasa, masraf ve ödenen bilanço özeti.\n"
            "• <code>/rapor</code> : Tüm grupların detaylı durum raporu.\n"
            "• <code>/ekstre [Cari] [Gün]</code> : Çok günlük cari hesap ekstresi.\n"
            "• <code>/yenigun</code> : 🌅 Kalan kasayı devire aktararak yeni günü açar.\n"
            "• <code>/kapanis</code> : 🌙 Kurucuya özel gün sonu kapanış bilançosu.\n\n"
            "🪙 <b>KRİPTO, KUR VE İBAN ARAÇLARI</b>\n"
            "• <code>/kur</code> : Canlı borsa USDT/TRY ve Kapalıçarşı Dolar kurları.\n"
            "• <code>/hesap [Grup] [Kom%] [Kur]</code> : Tether hesap makinesi.\n"
            "• <code>/iban</code> : Şirket İBAN listesi.\n"
            "• <code>/ibantahsis [Hesap] [Cari]</code> : İBAN'ı cariye tahsis eder.\n"
            "• <code>/ibanbosalt [Hesap]</code> : İBAN'ı boşa çıkarır.\n"
            "• <code>/ibancoz [İBAN]</code> : İBAN doğrulama ve banka tespiti.\n"
            "• <code>/t</code> : Canlı TRC-20 rezerv ve bakiye raporu.\n"
            "• <code>/qr [Cüzdan]</code> : Cüzdan QR kodu ve istihbarat analizi.\n"
            "• <code>/canlikur</code> : Dünya borsaları ve döviz kurları.\n\n"
            "🛡️ <b>YÖNETİCİ KONTROLLERİ</b>\n"
            "• <code>/adminler</code> : Yetkili yöneticileri listeler.\n"
            "• <code>/adminekle [ID] [İsim]</code> : Yeni yönetici ekler.\n"
            "• <code>/adminsil [ID]</code> : Yöneticiyi siler.\n"
            "• <code>/kapanissaati [SS:DD]</code> : Otomatik rapor saatini ayarlar.\n"
            "• <code>/panel</code> : Canlı Web Dashboard linki.\n"
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
    tum_veriler = sayfa.get_all_values()
    
    for i, row in enumerate(tum_veriler[1:], start=2):
        if len(row) >= 2 and normalize_text(row[1]) == hedef_norm:
            mevcut_val = guvenliSayi(row[sutun_idx - 1]) if len(row) >= sutun_idx else 0.0
            yeni_val = round(mevcut_val + (tutar * carp), 2)
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
                f"✅ <b>{isim} Başarılı!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
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
    tum_veriler = sayfa.get_all_values()
    
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
        sayfa.update_cell(bos_satir, 9, masraf_ham.upper())
        sayfa.update_cell(bos_satir, 10, tutar_yuvarlanmis)
        
        app_state["SON_ISLEM"] = {
            "sayfa": sayfa.title, "satir": bos_satir, "sutun": 10,
            "eskiDeger": 0, "grupAdi": masraf_ham.upper(),
            "islemTuru": "Masraf Ekleme", "is_new_masraf": True
        }
        sistemeLogYaz("Masraf Ekleme", f"{masraf_ham.upper()} | {paraFormatla(tutar_yuvarlanmis)}")
        
        return (
            f"✅ <b>Masraf Eklendi!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
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
            sayfa.update_cell(bulunan_i, 9, "")
            sayfa.update_cell(bulunan_i, 10, "")
            app_state["SON_ISLEM"] = {
                "sayfa": sayfa.title, "satir": bulunan_i, "sutun": 10,
                "eskiDeger": mevcut, "eskiAd": col_i, "grupAdi": col_i,
                "islemTuru": "Masraf Silme", "is_masraf_update": True
            }
            sistemeLogYaz("Masraf Silme", f"{col_i} | Tamamı Silindi ({paraFormatla(mevcut)})")
            return (
                f"🗑️ <b>Masraf Satırı Silindi!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
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
                f"✅ <b>Masraf Tutarı Düşüldü!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
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
    veriler = sayfa.get_all_values()
    
    finans = tablodan_finans_ozeti_hesapla(veriler)
    saat = suankiZamaniAl().strftime("%H:%M")
    
    return (
        f"📊 <b>GÜNLÜK FİNANS BİLANÇOSU</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 Tarih: {sayfa.title} | ⏰ Saat: {saat}\n"
        f"🏢 Aktif Grup Sayısı: {len(finans['aktif_gruplar'])}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔄 Toplam Devir: {paraFormatla(finans['devir'])}\n"
        f"💰 Eklenen Kasa: {paraFormatla(finans['kasa'])}\n"
        f"💸 Toplam Ödeme: {paraFormatla(finans['odenen'])}\n"
        f"✂️ Toplam Komisyon: {paraFormatla(finans['komisyon'])}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 <b>NET KALAN KASA: {paraFormatla(finans['kalan'])}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <i>Tüm grupların anlık genel toplamıdır.</i>"
    )

def tumGruplarRaporu_impl() -> str:
    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    veriler = sayfa.get_all_values()
    
    finans = tablodan_finans_ozeti_hesapla(veriler)
    saat = suankiZamaniAl().strftime("%H:%M")
    
    mesaj = (
        f"📊 <b>GÜNLÜK DETAYLI GRUP RAPORU</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 Tarih: {sayfa.title} | ⏰ Saat: {saat}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
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
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 <b>GENEL TOPLAM BİLANÇO</b>\n"
        f"🔄 Toplam Devir: {paraFormatla(finans['devir'])}\n"
        f"💰 Toplam Kasa: {paraFormatla(finans['kasa'])}\n"
        f"💸 Toplam Ödeme: {paraFormatla(finans['odenen'])}\n"
        f"✂️ Toplam Komisyon: {paraFormatla(finans['komisyon'])}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 <b>NET KALAN KASA: {paraFormatla(finans['kalan'])}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    return mesaj

def masrafRaporuUret_impl() -> str:
    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    veriler = sayfa.get_all_values()
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
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    for m in masraflar:
        mesaj += f"🔹 <b>{m['ad']}:</b> {paraFormatla(m['fiyat'])}\n"
    mesaj += (
        f"\n━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Toplam Kalem: <b>{len(masraflar)} Adet</b>\n"
        f"📊 <b>TOPLAM GİDER: {paraFormatla(toplam)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    return mesaj

def gun_sonu_kapanis_raporu_uret() -> str:
    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    veriler = sayfa.get_all_values()
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
    except:
        pass

    tarih = sayfa.title
    saat = suankiZamaniAl().strftime("%H:%M")
    
    rapor = (
        f"🌙 <b>GÜN SONU FİNANS VE KASA BİLANÇOSU</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 <b>Tarih:</b> {tarih} | ⏰ <b>Saat:</b> {saat}\n"
        f"🏢 <b>İşlem Gören Grup:</b> {len(finans['aktif_gruplar'])} Adet\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔄 <b>Toplam Devir:</b> {paraFormatla(finans['devir'])}\n"
        f"💰 <b>Eklenen Kasa:</b> {paraFormatla(finans['kasa'])}\n"
        f"💸 <b>Toplam Ödeme:</b> {paraFormatla(finans['odenen'])}\n"
        f"✂️ <b>Toplam Komisyon:</b> {paraFormatla(finans['komisyon'])}\n"
        f"📉 <b>Toplam Masraf:</b> {paraFormatla(toplam_masraf)} <i>({len(masraflar)} Kalem)</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 <b>GÜN SONU NET KALAN: {paraFormatla(finans['kalan'])}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
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
                rapor += f"   └ <i>({' | '.join(detay_parts)})</i>\n"
        rapor += "\n"
        
    if masraflar:
        rapor += "📉 <b>ÖNE ÇIKAN MASRAFLAR:</b>\n"
        for m in masraflar[:5]:
            rapor += f"🔹 {m['ad']}: {paraFormatla(m['fiyat'])}\n"
        rapor += "\n"
        
    if anlik_kur_str:
        rapor += f"━━━━━━━━━━━━━━━━━━━━\n{anlik_kur_str}"
        
    rapor += "━━━━━━━━━━━━━━━━━━━━\n💡 <i>Yeni güne devretmek için: /yenigun</i>"
    return rapor

def f_tl(val) -> str:
    try:
        s = f"{float(val):.2f}"
        return s.replace(".", ",") + " ₺"
    except:
        return "- ₺"

def get_harem_dolar_kuru() -> Tuple[float, float]:
    """Harem Altın / Kapalıçarşı Serbest Piyasa Doları (USD/TRY) Alış ve Satış kurlarını çeker."""
    try:
        data = http_get_json("https://finans.truncgil.com/v3/today.json")
        usd_info = data.get("USD", {})
        alis_str = str(usd_info.get("Buying", "")).replace(".", "").replace(",", ".")
        satis_str = str(usd_info.get("Selling", "")).replace(".", "").replace(",", ".")
        alis = float(alis_str)
        satis = float(satis_str)
        if alis > 0 and satis > 0:
            return alis, satis
    except Exception as e:
        print(f"Harem/Kapalıçarşı Dolar kuru çekme hatası: {e}")
        
    try:
        fiat = http_get_json("https://api.exchangerate-api.com/v4/latest/USD")["rates"]
        rate = float(fiat.get("TRY", 48.09))
        return rate - 0.05, rate + 0.05
    except Exception:
        return 48.20, 48.25

def kurRaporuUret_impl() -> str:
    yanit = "📊 <b>GÜNCEL DÖVİZ & USDT KURLARI</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    
    # 1. Harem Altın / Kapalıçarşı Serbest Piyasa Doları (USD/TRY)
    try:
        h_alis, h_satis = get_harem_dolar_kuru()
        yanit += (
            f"🏛️ <b>HAREM (Kapalıçarşı Doları)</b>\n"
            f"💵 Alış: <b>{f_tl(h_alis)}</b> | Satış: <b>{f_tl(h_satis)}</b>\n\n"
        )
    except Exception:
        pass

    # 2. Binance
    try:
        r = http_get_json("https://data-api.binance.vision/api/v3/ticker/24hr?symbol=USDTTRY")
        yanit += (
            f"🟡 <b>BİNANCE USDT/TRY</b>\n"
            f"💵 Anlık Kur: {f_tl(r.get('lastPrice'))}\n"
            f"🔺 24saat En Yüksek: {f_tl(r.get('highPrice'))}\n"
            f"🔻 24saat En Düşük: {f_tl(r.get('lowPrice'))}\n\n"
        )
    except Exception as e:
        yanit += "🟡 <b>BİNANCE USDT/TRY</b>\n⚠️ Veri çekilemedi.\n\n"

    # 3. Paribu
    try:
        r = http_get_json("https://www.paribu.com/ticker")["USDT_TL"]
        yanit += (
            f"🔵 <b>PARİBU USDT/TRY</b>\n"
            f"💵 Anlık Kur: {f_tl(r.get('last'))}\n"
            f"🔺 24saat En Yüksek: {f_tl(r.get('high24hr'))}\n"
            f"🔻 24saat En Düşük: {f_tl(r.get('low24hr'))}\n\n"
        )
    except Exception as e:
        yanit += "🔵 <b>PARİBU USDT/TRY</b>\n⚠️ Veri çekilemedi.\n\n"

    # 4. BtcTurk
    try:
        r = http_get_json("https://api.btcturk.com/api/v2/ticker?pairSymbol=USDT_TRY")["data"][0]
        yanit += (
            f"🟢 <b>BTCTÜRK USDT/TRY</b>\n"
            f"💵 Anlık Kur: {f_tl(r.get('last'))}\n"
            f"🔺 24saat En Yüksek: {f_tl(r.get('high'))}\n"
            f"🔻 24saat En Düşük: {f_tl(r.get('low'))}\n\n"
        )
    except Exception as e:
        yanit += "🟢 <b>BTCTÜRK USDT/TRY</b>\n⚠️ Veri çekilemedi.\n\n"

    # 5. WhiteBIT
    try:
        r = http_get_json("https://whitebit.com/api/v1/public/ticker?market=USDT_TRY")["result"]
        yanit += (
            f"⚪ <b>WHITEBIT USDT/TRY</b>\n"
            f"💵 Anlık Kur: {f_tl(r.get('last'))}\n"
            f"🔺 24saat En Yüksek: {f_tl(r.get('high'))}\n"
            f"🔻 24saat En Düşük: {f_tl(r.get('low'))}\n\n"
        )
    except Exception as e:
        yanit += "⚪ <b>WHITEBIT USDT/TRY</b>\n⚠️ Veri çekilemedi.\n\n"

    # 6. OKX
    try:
        r = http_get_json("https://www.okx.com/api/v5/market/ticker?instId=USDT-TRY")["data"][0]
        yanit += (
            f"⚫ <b>OKX USDT/TRY</b>\n"
            f"💵 Anlık Kur: {f_tl(r.get('last'))}\n"
            f"🔺 24saat En Yüksek: {f_tl(r.get('high24h'))}\n"
            f"🔻 24saat En Düşük: {f_tl(r.get('low24h'))}\n\n"
        )
    except Exception as e:
        pass

    return yanit.strip()

def canliKurSorgula_impl() -> str:
    try:
        b_usdt = http_get_json("https://data-api.binance.vision/api/v3/ticker/price?symbol=USDTTRY")
        b_btc = http_get_json("https://data-api.binance.vision/api/v3/ticker/price?symbol=BTCUSDT")
        b_eth = http_get_json("https://data-api.binance.vision/api/v3/ticker/price?symbol=ETHUSDT")
        fiat = http_get_json("https://api.exchangerate-api.com/v4/latest/USD")["rates"]
        try_rate = fiat.get("TRY", 0)
        return (
            "🌍 <b>CANLI PİYASA & DÜNYA KURLARI</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "🪙 <b>Kripto Paralar (Binance)</b>\n"
            f"🇹🇷 USDT / TRY: <code>{float(b_usdt['price']):.2f} ₺</code>\n"
            f"🔶 BTC / USDT: <code>{float(b_btc['price']):,.0f} $</code>\n"
            f"🔷 ETH / USDT: <code>{float(b_eth['price']):.2f} $</code>\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "💵 <b>Dünya Para Birimleri</b>\n"
            f"🇺🇸 Dolar (USD): <code>{try_rate:.2f} ₺</code>\n"
            f"🇪🇺 Euro (EUR): <code>{(try_rate / fiat.get('EUR', 1)):.2f} ₺</code>\n"
            f"🇬🇧 Sterlin (GBP): <code>{(try_rate / fiat.get('GBP', 1)):.2f} ₺</code>\n"
            f"🇨🇭 İsviçre Frangı (CHF): <code>{(try_rate / fiat.get('CHF', 1)):.2f} ₺</code>\n"
            f"🇨🇦 Kanada Dol. (CAD): <code>{(try_rate / fiat.get('CAD', 1)):.2f} ₺</code>\n"
            f"🇦🇺 Avustralya Dol. (AUD): <code>{(try_rate / fiat.get('AUD', 1)):.2f} ₺</code>\n"
            f"🇯🇵 Japon Yeni (JPY): <code>{(try_rate / fiat.get('JPY', 1)):.2f} ₺</code>\n"
            f"🇸🇦 Suudi Riyali (SAR): <code>{(try_rate / fiat.get('SAR', 1)):.2f} ₺</code>\n"
            f"🇷🇺 Rus Rublesi (RUB): <code>{(try_rate / fiat.get('RUB', 1)):.2f} ₺</code>\n\n"
            f"<i>⏱ Son Güncelleme: {suankiZamaniAl().strftime('%H:%M:%S')}</i>"
        )
    except Exception as e:
        return f"❌ <b>API Hatası:</b> {e}"

def analyze_tron_wallet(address: str) -> str:
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
    borsalar = [
        ("🟡 <b>BİNANCE</b>", "https://data-api.binance.vision/api/v3/ticker/price?symbol=USDTTRY", lambda r: float(r["price"])),
        ("🔵 <b>PARİBU</b>", "https://www.paribu.com/ticker", lambda r: float(r["USDT_TL"]["last"])),
        ("🟢 <b>BTCTÜRK</b>", "https://api.btcturk.com/api/v2/ticker?pairSymbol=USDT_TRY", lambda r: float(r["data"][0]["last"])),
        ("⚪ <b>WHITEBIT</b>", "https://whitebit.com/api/v1/public/ticker?market=USDT_TRY", lambda r: float(r["result"]["last"])),
        ("⚫ <b>OKX</b>", "https://www.okx.com/api/v5/market/ticker?instId=USDT-TRY", lambda r: float(r["data"][0]["last"]))
    ]
    
    def format_sayi_yerel(val):
        return f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    default_rate = 48.09
    try:
        fiat = http_get_json("https://api.exchangerate-api.com/v4/latest/USD")["rates"]
        default_rate = float(fiat.get("TRY", 48.09))
    except Exception:
        pass

    satirlar = []
    fiyatlar = []
    for isim, url, parser in borsalar:
        try:
            d = http_get_json(url)
            val = parser(d)
            satirlar.append(f"{isim} USDT/TRY - 💵 Anlık Kur: {format_sayi_yerel(val)} ₺")
            fiyatlar.append(val)
        except Exception:
            satirlar.append(f"{isim} USDT/TRY - 💵 Anlık Kur: {format_sayi_yerel(default_rate)} ₺")
            fiyatlar.append(default_rate)
            
    referans_kur = fiyatlar[0] if fiyatlar else default_rate
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
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 <b>Tarih/Saat:</b> {tarih_saat}\n"
        f"🌐 <b>Ağ:</b> TRON (TRC-20)\n"
        f"📌 <b>Cüzdan:</b> <code>{cuzdan_adresi}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{borsa_kurlari_metni}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 <b>USDT Bakiyesi:</b> <code>{usdt_format} USDT</code>\n"
        f"⚡ <b>TRX Bakiyesi:</b> <code>{trx_format} TRX</code>\n"
        f"🌍 <b>Toplam Varlık (USD):</b> <code>${usd_format}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🇹🇷 <b>USDT TÜRK LİRASI KARŞILIĞI:</b>\n"
        f"💰 <b>{usdt_tl_format} ₺</b> <i>(1 USDT ≈ {kur_format} ₺)</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
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
    if len(parcalar) < 2:
        telegramMesajGonder(
            chat_id,
            "⚠️ <b>Hatalı Kullanım!</b>\n"
            "Lütfen QR koda dönüştürmek istediğiniz borsa/cüzdan adresini girin.\n\n"
            "📌 <b>Örnek Kullanım:</b>\n"
            "<code>/qr TQHuwJh5c4ygbKhfFoGqTZTahjQuJAX3iV</code>"
        )
        return
        
    cuzdan_adresi = parcalar[1].strip()
    if len(cuzdan_adresi) < 10:
        telegramMesajGonder(chat_id, "⚠️ <b>Geçersiz Cüzdan Adresi:</b> Lütfen geçerli bir borsa veya cüzdan adresi giriniz.")
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
        trx_bal, usdt_bal, total_usd = get_tron_balances(cuzdan_adresi)
        borsa_analiz = analyze_tron_wallet(cuzdan_adresi)
        borsa_metni = f"{borsa_analiz}\n"
        bakiye_metni = (
            f"💰 <b>HESAPTAKİ ANLIK VARLIKLAR:</b>\n"
            f"💵 <b>USDT (TRC20):</b> <code>{usdt_bal:,.2f} USDT</code>\n"
            f"🪙 <b>TRX Bakiyesi:</b> <code>{trx_bal:,.2f} TRX</code>\n"
            f"📊 <b>Toplam Cüzdan Değeri:</b> <code>~{total_usd:,.2f} $</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
        )

    qr_foto_url = f"https://api.qrserver.com/v1/create-qr-code/?size=500x500&data={urllib.parse.quote(cuzdan_adresi)}&margin=15"
    
    caption = (
        f"⚡ <b>CÜZDAN ADRESİ & CANLI BAKİYE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
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
        telegramFotoGonder(chat_id, fallback_qr, caption, klavye)

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
        f"🧮 <b>HESAP KESİM RAPORU</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏢 Grup: <b>{gercekGrupAdi.upper()}</b>\n"
        f"🕒 Zaman: {islemZamani}\n\n"
    )
    if devirBorc != 0:
        mesaj += (
            f"⚠️ <b>DEVİR / BORÇ HATIRLATMASI</b> ⚠️\n"
            f"Geçmişten Kalan: <b>{paraFormatla(devirBorc)}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
        )
    mesaj += (
        f"💵 Güncel Kasa: {paraFormatla(guncelKasa)}\n"
        f"📉 Komisyon (% {komisyonOrani}): {paraFormatla(komisyonKesintisi)}\n"
        f"✅ Net Bakiye (TL): {paraFormatla(netKasaTl)}\n"
        f"💱 İşlem Kuru: {kur}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>Tether Karşılığı: {rakamFormatla(duzUsdt)} USDT</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    return mesaj

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
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 <b>Banka:</b> {banka_adi_str}\n"
        f"📊 <b>Durum:</b> {durum_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>Okunabilir Format (Boşluklu):</b>\n"
        f"<code>{bosluklu_iban}</code>\n\n"
        f"⚡ <b>Hızlı Kopyala (Mobil Bankacılık):</b>\n"
        f"<code>{bitisik_iban}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )
    if banka_kodu:
        mesaj += (
            f"🏛️ <b>Banka Kodu:</b> <code>{banka_kodu}</code>\n"
            f"🏢 <b>Şube / Hesap No:</b> <code>{sube_hesap}</code>\n"
        )
    mesaj += (
        f"📑 <b>Şirket Envanteri:</b> {sirket_durumu}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <i>Kopyalamak için numaranın üzerine dokunabilirsiniz.</i>"
    )
    return mesaj

def ibanListesiGetir_impl() -> str:
    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    veriler = sayfa.get_all_values()
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
    mesaj = "🏦 <b>ŞİRKET İBAN LİSTESİ</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    mesaj += "🟢 <b>BOŞTAKİ İBANLAR</b> <i>(Kullanıma Hazır)</i>\n" + ("\n".join(bosta) if bosta else "🔹 <i>Boşta İBAN yok.</i>") + "\n\n"
    mesaj += "🔴 <b>KULLANIMDAKİ İBANLAR</b>\n" + ("\n".join(dolu) if dolu else "🔹 <i>Kullanımda İBAN yok.</i>")
    return mesaj

def iban_hesap_bul(veriler: List[List[str]], aranan_kod: str):
    """
    Excel tablosundaki L (12. sütun) ve P (16. sütun) hesap bloklarında arama yapar.
    Döner: (satir_no_1based, hedef_cari_sutun_1based, hesap_adi, mevcut_cari)
    """
    aranan_norm = normalize_text(aranan_kod)
    if not aranan_norm:
        return None
        
    for idx, row in enumerate(veriler[1:], start=2):
        # 1. Sol Blok: Sütun L (Col 12), Cari Sütun O (Col 15)
        if len(row) > 11 and row[11].strip():
            h_ad = row[11].strip()
            h_norm = normalize_text(h_ad)
            if aranan_norm == h_norm or aranan_norm in h_norm or h_norm.startswith(aranan_norm):
                mevcut_cari = row[14].strip() if len(row) > 14 else ""
                return idx, 15, h_ad, mevcut_cari
                
        # 2. Sağ Blok: Sütun P (Col 16), Cari Sütun R (Col 18)
        if len(row) > 15 and row[15].strip():
            h_ad = row[15].strip()
            h_norm = normalize_text(h_ad)
            if aranan_norm == h_norm or aranan_norm in h_norm or h_norm.startswith(aranan_norm):
                mevcut_cari = row[17].strip() if len(row) > 17 else ""
                return idx, 18, h_ad, mevcut_cari
                
    return None

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
    veriler = sayfa.get_all_values()
    
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
    
    sayfa.update_cell(satir_idx, col_idx, cari_temiz)
    
    app_state["SON_ISLEM"] = {
        "sayfa": sayfa.title, "satir": satir_idx, "sutun": col_idx,
        "eskiDeger": eski_cari, "grupAdi": hesap_adi, "islemTuru": "İBAN Tahsis"
    }
    sistemeLogYaz("İBAN Tahsis", f"{hesap_adi} ➔ {cari_temiz}")
    
    return (
        f"✅ <b>İBAN BAŞARIYLA TAHSİS EDİLDİ!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 <b>Hesap:</b> <code>{hesap_adi}</code>\n"
        f"👤 <b>Tahsis Edilen Cari:</b> <b>{cari_temiz}</b>\n"
        f"📊 <b>Durum:</b> 🔴 <b>Kullanımda</b>\n"
        f"📌 <b>Excel Satırı:</b> Satır {satir_idx}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
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
    sh = get_spreadsheet()
    sayfa = get_active_daily_sheet(sh)
    veriler = sayfa.get_all_values()
    
    bulunan = iban_hesap_bul(veriler, hesap_kodu)
    if not bulunan:
        return f"⚠️ <b>Hesap Bulunamadı!</b>\nExcel tablosunda '<b>{hesap_kodu}</b>' adlı bir İBAN/hesap bulunamadı."
        
    satir_idx, col_idx, hesap_adi, eski_cari = bulunan
    
    sayfa.update_cell(satir_idx, col_idx, "")
    
    app_state["SON_ISLEM"] = {
        "sayfa": sayfa.title, "satir": satir_idx, "sutun": col_idx,
        "eskiDeger": eski_cari, "grupAdi": hesap_adi, "islemTuru": "İBAN Boşaltma"
    }
    sistemeLogYaz("İBAN Boşaltma", f"{hesap_adi} | Eski: {eski_cari} ➔ Boş")
    
    eski_str = f"<s>{eski_cari}</s>" if eski_cari else "<i>(Zaten boştu)</i>"
    return (
        f"🟢 <b>İBAN BOŞA ÇIKARILDI!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 <b>Hesap:</b> <code>{hesap_adi}</code>\n"
        f"👤 <b>Eski Cari:</b> {eski_str}\n"
        f"📊 <b>Durum:</b> 🟢 <b>Müsait / Kullanıma Hazır</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <i>Hesap havuza geri döndü, başka bir cariye verilebilir.</i>"
    )

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
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
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
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>{len(secilen_sayfalar)} GÜNLÜK TOPLAM PERFORMANS:</b>\n"
        f"💰 Toplam Giriş: <b>{paraFormatla(toplam_giris)}</b>\n"
        f"💸 Toplam Ödeme: <b>{paraFormatla(toplam_odeme)}</b>\n"
    )
    if abs(toplam_komisyon) > 0.001:
        mesaj += f"✂️ Toplam Komisyon: <b>{paraFormatla(toplam_komisyon)}</b>\n"
    mesaj += (
        f"🏦 <b>GÜNCEL NET BAKİYE: {paraFormatla(en_guncel_kalan)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━"
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
        return f"🌐 <b>YAPAY ZEKA ÇEVİRİSİ</b>\n━━━━━━━━━━━━━━━━━━━━\n\n📝 <b>Orijinal Metin:</b>\n<i>{cevrilecek}</i>\n\n🎯 <b>{etiket}:</b>\n<code>{son_ceviri}</code>"
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
        except: pass

    klavye = {
        "inline_keyboard": [
            [{"text": "🔄 Masrafları Temizle & Yeni Güne Geç", "callback_data": "yenigun_onay_sil"}],
            [{"text": "📋 Masrafları Koru & Yeni Güne Geç", "callback_data": "yenigun_onay_tut"}],
            [{"text": "❌ İptal Et", "callback_data": "yenigun_iptal"}]
        ]
    }
    return (
        f"🌅 <b>YENİ GÜN DEVİR İŞLEMİ ➔ {hedef_tarih}</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
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
        except: pass
        
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
    except:
        adminSayfasi = sh.add_worksheet(title=ADMIN_SAYFASI, rows=100, cols=4)
        adminSayfasi.append_row(["Telegram ID", "Yönetici Adı", "Ekleyen", "Tarih"])
        
    rows = adminSayfasi.get_all_values()
    for r in rows[1:]:
        if len(r) > 0 and r[0].strip() == str(yeni_id):
            return f"⚠️ <b>{yeni_id}</b> zaten yetkili yöneticiler arasında!"
            
    adminSayfasi.append_row([str(yeni_id), isim, "KURUCU", bugununTarihiniAl()])
    app_state["EK_ADMINLER"].add(yeni_id)
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

# --- YARDIMCI: HIZLI VE GÜVENLİ ÇALIŞTIRICI ---
def islemi_analiz_bildirimiyle_yap(chat_id: int, islem_fn, *args, goster_bildirim: bool = False):
    msg_id = None
    if goster_bildirim:
        yukleniyor = telegramMesajGonder(chat_id, "⏳ <b>Veriler analiz ediliyor, lütfen bekleyin...</b>")
        msg_id = yukleniyor.get("result", {}).get("message_id") if yukleniyor.get("ok") else None
    
    try:
        sonuc = islem_fn(*args)
        if isinstance(sonuc, tuple):
            text, markup = sonuc
            telegramMesajGonder(chat_id, text, markup)
        else:
            telegramMesajGonder(chat_id, str(sonuc))
    except Exception as e:
        telegramMesajGonder(chat_id, f"❌ <b>Hata:</b> {e}")
    finally:
        if msg_id:
            telegramMesajSil(chat_id, msg_id)

# --- UPDATE DISPATCHER ---
def process_telegram_update(update: dict):
    if "callback_query" in update:
        cq = update["callback_query"]
        data = cq.get("data", "")
        chat_id = cq["message"]["chat"]["id"]
        user_id = cq["from"]["id"]
        
        telegram_api("answerCallbackQuery", {"callback_query_id": cq["id"]})
        
        if data.startswith("t_yenile_"):
            cuzdan = data.replace("t_yenile_", "").strip()
            islemi_analiz_bildirimiyle_yap(chat_id, trc20_varlik_raporu_uret, cuzdan)
            return

        if not yetkili_mi(user_id):
            telegramMesajGonder(chat_id, "⛔ <b>Erişim Reddedildi!</b>\nBu işlem için yetkiniz bulunmamaktadır.")
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
        elif data == "menu_yenigun":
            metin, klavye = yenigun_baslat_mesaji()
            telegramMesajGonder(chat_id, metin, klavye)
        elif data == "yenigun_onay_sil":
            islemi_analiz_bildirimiyle_yap(chat_id, yenigun_gerceklestir_impl, True)
        elif data == "yenigun_onay_tut":
            islemi_analiz_bildirimiyle_yap(chat_id, yenigun_gerceklestir_impl, False)
        elif data == "yenigun_iptal":
            telegramMesajGonder(chat_id, "❌ Yeni gün devir işlemi iptal edildi.")
        elif data.startswith("rapor_"):
            grup = data.replace("rapor_", "")
            islemi_analiz_bildirimiyle_yap(chat_id, grup_kasa_analiz_fisi_uret, grup)
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

        # /t veya /rezerv veya /varlik (TRC-20 Canlı Rezerv & Varlık Raporu)
        if ana_komut in ["/t", "/rezerv", "/varlik"]:
            cuzdan = komut_parcalari[1].strip() if len(komut_parcalari) > 1 else VARSAYILAN_TRC20_ADRES
            islemi_analiz_bildirimiyle_yap(chat_id, trc20_varlik_raporu_uret, cuzdan, goster_bildirim=True)
            return

        # /grupbagla veya /bagla
        if ana_komut in ["/grupbagla", "/bagla"]:
            if not yetkili_mi(user_id):
                telegramMesajGonder(chat_id, f"⛔ <b>Yetkisiz İşlem:</b> Telegram grubunu Excel'e bağlama yetkisi sadece şirket yöneticilerine aittir.\nKullanıcı ID: <code>{user_id}</code>")
                return
            islemi_analiz_bildirimiyle_yap(chat_id, grup_bagla_impl, chat_id, user_id, text, chat_title)
            return

        # /grupkopar veya /baglantikes
        if ana_komut in ["/grupkopar", "/baglantikes", "/grupbaglasil"]:
            if not yetkili_mi(user_id):
                telegramMesajGonder(chat_id, f"⛔ <b>Yetkisiz İşlem:</b> Grup bağlantısını kaldırma yetkisi sadece şirket yöneticilerine aittir.\nKullanıcı ID: <code>{user_id}</code>")
                return
            islemi_analiz_bildirimiyle_yap(chat_id, grup_kopar_impl, chat_id, user_id)
            return

        # /grupbaglantilari veya /gruplar
        if ana_komut in ["/grupbaglantilari", "/gruplar", "/baglantilar"]:
            if not yetkili_mi(user_id):
                telegramMesajGonder(chat_id, f"⛔ <b>Yetkisiz İşlem:</b> Bu listeyi sadece şirket yöneticileri görebilir.")
                return
            islemi_analiz_bildirimiyle_yap(chat_id, grup_baglantilari_listesi_impl)
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
                        if not yetkili_mi(user_id):
                            telegramMesajGonder(chat_id, f"⛔ <b>Erişim Reddedildi!</b>\nKullanıcı ID'niz: <code>{user_id}</code>")
                            return
                        telegramMesajGonder(
                            chat_id,
                            "💡 <b>Kasa Komutu Kullanım Rehberi:</b>\n━━━━━━━━━━━━━━━━━━━━\n"
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
                    if not yetkili_mi(user_id):
                        telegramMesajGonder(chat_id, f"⛔ <b>Yetkisiz İşlem:</b> Kasaya para ekleme işlemi sadece yöneticilere açıktır.")
                        return
                    if chat_id in baglantilar:
                        grup_adi = baglantilar[chat_id]["grup"]
                        islemi_analiz_bildirimiyle_yap(chat_id, hucreyeVeriYaz_impl, f"/kasa {grup_adi} {args[0]}", 4, "Kasa Ekleme", 1)
                        return
                    else:
                        telegramMesajGonder(chat_id, "⚠️ Lütfen hangi gruba işlem yapıldığını belirtin! Örnek: <code>/kasa SACİD 1500</code>")
                        return

            # 3. İki veya daha fazla parametre girildiğinde (/kasa SACİD 1500)
            else:
                if not yetkili_mi(user_id):
                    telegramMesajGonder(chat_id, f"⛔ <b>Yetkisiz İşlem:</b> Kasaya para ekleme işlemi sadece yöneticilere açıktır.")
                    return
                islemi_analiz_bildirimiyle_yap(chat_id, hucreyeVeriYaz_impl, text, 4, "Kasa Ekleme", 1)
                return

        # Diğer tüm komutlar için yönetici yetkisi zorunludur
        if not yetkili_mi(user_id):
            telegramMesajGonder(
                chat_id,
                f"⛔ <b>Erişim Reddedildi!</b>\n"
                f"Bu işlem sadece yetkili şirket yöneticilerine özeldir.\n"
                f"Kullanıcı ID'niz: <code>{user_id}</code>"
            )
            return

        if ana_komut in ["/start", "/menu", "/menü"]:
            telegramMesajGonder(chat_id, "👋 <b>CFO ve Finans Yönetim Botu</b>\nLütfen bir işlem seçin:\n\n👨💻 <i>Yazılım: @CRYPTOATAKAN © 2026</i>", menuKlavyesiOlustur(is_group))
        elif ana_komut in ["/rehber", "/komutlar", "/yardim", "/yardım"]:
            telegramMesajGonder(chat_id, rehber_ana_metni(), rehber_ana_klavyesi())
        elif ana_komut in ["/qr", "/tronqr", "/tron", "/cuzdan", "/cüzdan", "/adres"]:
            cuzdanQrUret_impl(chat_id, text)
        elif ana_komut == "/panel":
            panel_btn = {"inline_keyboard": [[{"text": "🚀 Canlı CFO Panelini Aç", "url": WEB_APP_URL}]]}
            telegramMesajGonder(
                chat_id,
                f"🌐 <b>CANLI CFO WEB DASHBOARD</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <i>Şirketinizin tüm finans ve kasa verilerini 7/24 canlı web panelinden anlık izleyebilirsiniz.</i>\n\n"
                f"🔗 <b>Panel Linki:</b>\n{WEB_APP_URL}",
                panel_btn
            )
        elif ana_komut == "/ozet":
            islemi_analiz_bildirimiyle_yap(chat_id, hizliOzetUret_impl)
        elif ana_komut == "/rapor":
            islemi_analiz_bildirimiyle_yap(chat_id, tumGruplarRaporu_impl, goster_bildirim=True)
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
        elif ana_komut in ["/ibantahsis", "/tahsis"]:
            islemi_analiz_bildirimiyle_yap(chat_id, iban_tahsis_impl, text)
        elif ana_komut in ["/ibanbosalt", "/bosalt", "/ibansil"]:
            islemi_analiz_bildirimiyle_yap(chat_id, iban_bosalt_impl, text)
        elif ana_komut in ["/ekstre", "/gecmis", "/hesapdokumu", "/dokum"]:
            islemi_analiz_bildirimiyle_yap(chat_id, cari_ekstre_impl, text, goster_bildirim=True)
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
        elif ana_komut in ["/kapanis", "/gunsonu"]:
            if user_id != KURUCU_ID:
                telegramMesajGonder(chat_id, "⛔ <b>Yetkisiz İşlem:</b> Gün sonu kapanış raporunu alma yetkisi sadece Şirket Kurucusuna aittir.")
                return
            islemi_analiz_bildirimiyle_yap(chat_id, gun_sonu_kapanis_raporu_uret, goster_bildirim=True)
        elif ana_komut in ["/kapanissaati", "/saatayar"]:
            if user_id != KURUCU_ID:
                telegramMesajGonder(chat_id, "⛔ <b>Yetkisiz İşlem:</b> Bu ayar sadece Şirket Kurucusuna aittir.")
                return
            p_args = text.split()[1:]
            if not p_args:
                telegramMesajGonder(
                    chat_id,
                    f"🕒 <b>Otomatik Kapanış Rapor Saati:</b> <code>{app_state.get('KAPANIS_SAATI', '23:00')}</code>\n\n"
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
        
        .stats-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:16px; margin-bottom:30px; }
        .stat-card { background:#111827; border:1px solid #1f2937; border-radius:16px; padding:20px; position:relative; overflow:hidden; }
        .stat-card::before { content:''; position:absolute; top:0; left:0; width:4px; height:100%; }
        .stat-devir::before { background:#6366f1; }
        .stat-kasa::before { background:#3b82f6; }
        .stat-odenen::before { background:#f59e0b; }
        .stat-komisyon::before { background:#ec4899; }
        .stat-kalan::before { background:#10b981; }
        .stat-label { font-size:13px; color:#9ca3af; font-weight:600; text-transform:uppercase; margin-bottom:8px; }
        .stat-value { font-size:24px; font-weight:800; color:#ffffff; }
        
        .section-title { font-size:18px; font-weight:700; color:#f8fafc; margin-bottom:16px; display:flex; align-items:center; gap:8px; }
        .groups-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(320px, 1fr)); gap:16px; margin-bottom:30px; }
        .group-card { background:#131d31; border:1px solid #202d46; border-radius:16px; padding:20px; }
        .group-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; padding-bottom:10px; border-bottom:1px solid #1f2d47; }
        .group-name { font-size:16px; font-weight:700; color:#60a5fa; display:flex; align-items:center; gap:8px; }
        .group-kalan-badge { background:rgba(16, 185, 129, 0.15); color:#34d399; padding:4px 10px; border-radius:8px; font-weight:700; font-size:14px; }
        
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
            const formatted = Math.abs(num).toLocaleString('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' ₺';
            return isNeg ? '-' + formatted : formatted;
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
                document.getElementById('toplam-devir').innerText = fmt(d.devir);
                document.getElementById('toplam-kasa').innerText = fmt(d.kasa);
                document.getElementById('toplam-odenen').innerText = fmt(d.odenen);
                document.getElementById('toplam-komisyon').innerText = fmt(d.komisyon);
                document.getElementById('toplam-kalan').innerText = fmt(d.kalan);

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
    print(f"CFO Bot & Canlı Dashboard Başlatıldı (7/24 Kesintisiz - Otomatik Kapanış Saati: {app_state.get('KAPANIS_SAATI', '23:00')})...")
    
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
