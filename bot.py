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

app_state = {
    "EK_ADMINLER": set(),
    "SISTEM_KILIDI": "PASIF",
    "CIRO_HEDEFI": None,
    "SON_ISLEM": None,
    "LOG_HAFTASI": None,
    "ADMIN_CACHE_TIME": 0
}

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def http_get_json(url: str, headers: dict = None) -> dict:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=12) as response:
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

def telegramMesajSil(chat_id, message_id):
    return telegram_api("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

def get_gspread_client():
    json_env = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if json_env and json_env.strip():
        try:
            info = json.loads(json_env.strip())
            creds = Credentials.from_service_account_info(info, scopes=SCOPES)
            return gspread.authorize(creds)
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
                return gspread.authorize(creds)
            except Exception as e:
                print(f"{path} okunamadı: {e}")

    raise FileNotFoundError("Google Service Account anahtarı bulunamadı!")

def get_spreadsheet():
    gc = get_gspread_client()
    return gc.open_by_key(SPREADSHEET_ID)

def is_valid_daily_sheet(ws) -> bool:
    """Bir sayfanın gerçek ana kasa tablosu (en az 8 sütun ve 30 satır) olup olmadığını doğrular."""
    if ws.title in [LOG_SAYFASI, "NOTLAR", "YEDEK", ADMIN_SAYFASI]:
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
        if abs(val) < 0.0001:
            return "0,00 ₺"
        is_negative = val < 0
        tam = int(abs(round(val, 2)))
        tam_str = f"{tam:,}".replace(",", ".")
        ondalik = f"{abs(val):.2f}".split(".")[1]
        
        if is_negative:
            return f"-{tam_str},{ondalik} ₺"
        else:
            return f"{tam_str},{ondalik} ₺"
    except:
        return "0,00 ₺"

def sistemeLogYaz(islemAdi: str, detay: str):
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

def rehber_metni():
    return (
        "📚 <b>SİSTEM KOMUT REHBERİ</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        "🏢 <b>KASA VE OPERASYON</b>\n"
        "• <code>/kasa TİGER 1500</code> : Kasaya para ekler (büyük/küçük harf farketmez).\n"
        "• <code>/kasasil TİGER 500</code> : Kasadan siler.\n"
        "• <code>/odeme TİGER 1000</code> : Ödenen tutarı girer.\n"
        "• <code>/odemesil TİGER 200</code> : Ödenen tutardan siler.\n"
        "• <code>/devir TİGER 5000</code> : Devir bakiyesi ekler.\n"
        "• <code>/devirsil TİGER 1000</code> : Devirden siler.\n"
        "• <code>/masrafekle Yemek 500</code> : Günlük masraf işler.\n"
        "• <code>/masrafsil Yemek 200</code> : Masraf siler.\n"
        "• <code>/masraf</code> : Günlük masraf listesini döker.\n"
        "• <code>/gerial</code> : En son işlemi geri alır.\n\n"
        "📊 <b>GÜNLÜK DÖNGÜ VE RAPORLAR</b>\n"
        "• <code>/yenigun</code> : 🌅 Yeni gün sayfasını açar, dünün Kalan Kasasını Devir'e aktarır.\n"
        "• <code>/rapor</code> : Tüm grupların güncel durum raporu.\n"
        "• <code>/ozet</code> : Kasa, Masraf ve Ödenen hızlı finans özeti.\n"
        "• <code>/log</code> veya <code>/son5</code> : Yapılan son işlemleri listeler.\n\n"
        "🌍 <b>EKSTRA ARAÇLAR</b>\n"
        "• <code>/canlikur</code> : 🌍 Dünya borsalarını ve kripto kurlarını getirir.\n"
        "• <code>/kur</code> : 🟡 Anlık USDT kurlarını listeler.\n"
        "• <code>/iban</code> : 🏦 Kullanımdaki ve boşta olan İBAN'ları listeler.\n"
        "• <code>/çeviri [Metin]</code> : 🌐 Otomatik çeviri yapar.\n"
        "• <code>/hesap SACİD 2 48.00</code> : Tether / Kasa hesap makinesi.\n"
        "• <code>/not [Metin]</code> : Şirket ajandasına not ekler.\n"
        "• <code>/notlar</code> : Son notları listeler.\n"
        "• <code>/panel</code> : Canlı CFO Web Dashboard linkini verir.\n"
        "• <code>/id</code> : Kendi Telegram kullanıcı ID'nizi öğrenirsiniz.\n\n"
        "🛡️ <b>YÖNETİCİ KONTROLLERİ</b>\n"
        "• <code>/adminekle [ID] [İsim]</code> : Yeni yönetici ekler.\n"
        "• <code>/adminsil [ID]</code> : Yöneticiyi siler.\n"
        "• <code>/adminler</code> : Yetkili yöneticileri listeler.\n\n"
        "💡 <i>Tüm işlemler arka planda güvenlik protokolüyle işlenmektedir.</i>"
    )

def parse_grup_ve_tutar(parametreler: List[str]) -> Tuple[str, float]:
    if len(parametreler) < 2:
        raise ValueError("Eksik bilgi! Örnek: <code>/kasa TİGER 1500</code>")
    try:
        tutar = float(parametreler[-1].replace(".", "").replace(",", "."))
        grup = " ".join(parametreler[:-1]).strip()
        return grup, tutar
    except ValueError:
        pass
    try:
        tutar = float(parametreler[0].replace(".", "").replace(",", "."))
        grup = " ".join(parametreler[1:]).strip()
        return grup, tutar
    except ValueError:
        raise ValueError("Lütfen geçerli bir sayısal tutar girin! Örnek: <code>/kasa TİGER 1500</code>")

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
            yeni_val = mevcut_val + (tutar * carp)
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
    
    for i, row in enumerate(tum_veriler[1:], start=2):
        col_i = row[8] if len(row) > 8 else ""
        col_j = row[9] if len(row) > 9 else ""
        if normalize_text(col_i) == hedef_norm:
            mevcut = guvenliSayi(col_j)
            yeni = mevcut + (tutar * carp)
            sayfa.update_cell(i, 10, yeni)
            app_state["SON_ISLEM"] = {"sayfa": sayfa.title, "satir": i, "sutun": 10, "eskiDeger": mevcut, "grupAdi": col_i, "islemTuru": isim}
            sistemeLogYaz(isim, f"{col_i} | {paraFormatla(tutar * carp)}")
            return f"✅ <b>{isim} Başarılı!</b>\n📉 Masraf Kalemi: <b>{col_i}</b>\n💵 İşlem Tutarı: <b>{paraFormatla(tutar * carp)}</b>\n\n<i>Hatalı işlem mi? /gerial yazabilirsiniz.</i>"
            
    bos_satir = len(tum_veriler) + 1
    for i, row in enumerate(tum_veriler[1:], start=2):
        col_i = row[8] if len(row) > 8 else ""
        if not col_i.strip():
            bos_satir = i
            break
    sayfa.update_cell(bos_satir, 9, masraf_ham.upper())
    sayfa.update_cell(bos_satir, 10, tutar)
    app_state["SON_ISLEM"] = {"sayfa": sayfa.title, "satir": bos_satir, "sutun": 10, "eskiDeger": 0, "grupAdi": masraf_ham, "islemTuru": "Yeni Masraf Ekleme"}
    sistemeLogYaz("Yeni Masraf Ekleme", f"{masraf_ham} | {paraFormatla(tutar)}")
    return f"✅ <b>Yeni Masraf Oluşturuldu!</b>\n📉 Masraf Kalemi: <b>{masraf_ham.upper()}</b>\n💵 Tutar: <b>{paraFormatla(tutar)}</b>\n\n<i>Hatalı işlem mi? /gerial yazabilirsiniz.</i>"

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

def kurRaporuUret_impl() -> str:
    yanit = "📊 <b>GÜNCEL KRİPTO KURLARI</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    try:
        r = http_get_json("https://data-api.binance.vision/api/v3/ticker/24hr?symbol=USDTTRY")
        yanit += f"🟡 <b>BİNANCE USDT/TRY</b>\n💵 Anlık: {float(r['lastPrice']):.2f} ₺ | 🔺 Yüksek: {float(r['highPrice']):.2f} ₺ | 🔻 Düşük: {float(r['lowPrice']):.2f} ₺\n\n"
    except: yanit += "🟡 <b>BİNANCE USDT/TRY</b>\n⚠️ Veri çekilemedi.\n\n"
    try:
        r = http_get_json("https://www.paribu.com/ticker")["USDT_TL"]
        yanit += f"🔵 <b>PARİBU USDT/TRY</b>\n💵 Anlık: {float(r['last']):.2f} ₺ | 🔺 Yüksek: {float(r['high24hr']):.2f} ₺ | 🔻 Düşük: {float(r['low24hr']):.2f} ₺\n\n"
    except: yanit += "🔵 <b>PARİBU USDT/TRY</b>\n⚠️ Veri çekilemedi.\n\n"
    try:
        r = http_get_json("https://api.btcturk.com/api/v2/ticker?pairSymbol=USDT_TRY")["data"][0]
        yanit += f"🟢 <b>BTCTÜRK USDT/TRY</b>\n💵 Anlık: {float(r['last']):.2f} ₺ | 🔺 Yüksek: {float(r['high']):.2f} ₺ | 🔻 Düşük: {float(r['low']):.2f} ₺\n\n"
    except: yanit += "🟢 <b>BTCTÜRK USDT/TRY</b>\n⚠️ Veri çekilemedi.\n\n"
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

# --- YENİ GÜN GEÇİŞİ (KUSURSUZ MATEMATİK & KOTA KORUMALI) ---
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
        "1. Dünkü <b>Kalan Kasa</b> (G sütunu) tutarları bugünkü <b>Devir</b> (C sütunu) hanesine aktarılacaktır.\n"
        "2. <b>Güncel Kasa (D), Ödenen (E) ve Komisyon (F)</b> sütunları tamamen sıfırlanacaktır (2-42. Satırlar).\n\n"
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
        if mevcut_sayfa.col_count < 8 or mevcut_sayfa.row_count < 30:
            sh.del_worksheet(mevcut_sayfa)
        else:
            return f"⚠️ <b>{hedef_yeni_tarih}</b> tarihli sayfa zaten mevcut ve kullanımda!"
    except gspread.exceptions.WorksheetNotFound:
        pass
        
    if not is_valid_daily_sheet(kaynak_sayfa):
        return "❌ Kopyalanacak geçerli bir kaynak finans sayfası bulunamadı!"
        
    # 3. Gerçek tam finans sayfasını kopyala
    yeni_sayfa = kaynak_sayfa.duplicate(new_sheet_name=hedef_yeni_tarih)
    veriler = kaynak_sayfa.get_all_values()
    
    toplam_devir = 0.0
    
    # 4. KUSURSUZ MATEMATİKSEL DEVİR & SIFIRLAMA MATRİSİ: C2:F42 (4 Sütun: Devir, Kasa, Ödenen, Komisyon)
    # Devir = Dünün Kalanı (G Sütunu), Kasa = 0, Ödenen = 0, Komisyon = 0
    # Böylece Kalan formülü (=C+D-E-F) doğrudan Devir'e eşitlenir; dünün komisyonu mükerrer düşmez!
    matrix_c_f = []
    for r_idx in range(2, 43):
        dunun_kalani = 0.0
        if r_idx - 1 < len(veriler):
            row = veriler[r_idx - 1]
            if len(row) >= 2:
                grup = row[1].strip()
                if grup and grup != "*" and "GENEL TOPLAM" not in grup.upper():
                    dunun_kalani = guvenliSayi(row[6]) if len(row) > 6 else 0.0
                    toplam_devir += dunun_kalani
        matrix_c_f.append([dunun_kalani, 0, 0, 0])
        
    # Tek seferde C2:F42 bloğunu güncelle (1 tek API çağrısı)
    yeni_sayfa.update('C2:F42', matrix_c_f, value_input_option='USER_ENTERED')
                
    # 5. Masrafları Temizleme Seçimi (I2:J42 tek seferde toplu temizleme)
    if masraflari_sil and yeni_sayfa.col_count >= 10:
        empty_masraf = [['', ''] for _ in range(41)]
        yeni_sayfa.update('I2:J42', empty_masraf)
            
    sistemeLogYaz("Yeni Gün Geçişi", f"Yeni gün ({hedef_yeni_tarih}) açıldı. Kaynak: {kaynak_sayfa.title} | Devir: {paraFormatla(toplam_devir)}")
    
    return (
        f"🌅 <b>{hedef_yeni_tarih} GÜNÜ BAŞARIYLA AÇILDI!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📁 <b>Kaynak Alınan Gün:</b> <code>{kaynak_sayfa.title}</code>\n"
        f"🔄 <b>Devir'e Aktarılan Kalan Kasa:</b> {paraFormatla(toplam_devir)}\n"
        f"💰 <b>Kasa, Ödenen ve Komisyon (2-42. Satırlar):</b> Sıfırlandı (Temiz Başlangıç)\n"
        f"📉 <b>Masraflar:</b> {'Temizlendi' if masraflari_sil else 'Korundu'}\n\n"
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

# --- YARDIMCI: ANALİZ BİLDİRİMİ İLE ÇALIŞTIRICI ---
def islemi_analiz_bildirimiyle_yap(chat_id: int, islem_fn, *args):
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
        
        if not yetkili_mi(user_id):
            telegramMesajGonder(chat_id, "⛔ <b>Erişim Reddedildi!</b>\nBu işlem için yetkiniz bulunmamaktadır.")
            return
            
        if data == "rehber":
            telegramMesajGonder(chat_id, rehber_metni())
        elif data == "rapor_ozet":
            islemi_analiz_bildirimiyle_yap(chat_id, hizliOzetUret_impl)
        elif data == "rapor_masraf":
            islemi_analiz_bildirimiyle_yap(chat_id, masrafRaporuUret_impl)
        elif data == "rapor_tumu":
            islemi_analiz_bildirimiyle_yap(chat_id, tumGruplarRaporu_impl)
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
            hedef_norm = normalize_text(grup)
            def tek_grup_rapor(grup_adi):
                sh = get_spreadsheet()
                sayfa = get_active_daily_sheet(sh)
                veriler = sayfa.get_all_values()
                for row in veriler[1:]:
                    if len(row) >= 2 and normalize_text(row[1]) == hedef_norm:
                        vals = [guvenliSayi(x) for x in row[1:7]]
                        while len(vals) < 6: vals.append(0.0)
                        devir, kasa, odenen, kom, kalan = vals[1], vals[2], vals[3], vals[4], vals[5]
                        emoji = grupEmojisiBul(row[1])
                        return (
                            f"{emoji} <b>{row[1].upper()} KASA ANALİZİ</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"📅 Tarih: {sayfa.title}\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"🔄 Devir: {paraFormatla(devir)}\n"
                            f"💰 Kasa: {paraFormatla(kasa)}\n"
                            f"💸 Ödenen: {paraFormatla(odenen)}\n"
                            f"✂️ Komisyon: {paraFormatla(kom)}\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"🏦 <b>NET KALAN: {paraFormatla(kalan)}</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━━"
                        )
                return f"⚠️ <b>{grup_adi}</b> grubu için veri bulunamadı."
            islemi_analiz_bildirimiyle_yap(chat_id, tek_grup_rapor, grup)
        return

    if "message" in update and "text" in update["message"]:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        user_id = msg["from"]["id"]
        text = msg["text"].strip()
        is_group = chat_id < 0

        if not text.startswith("/"):
            return

        komut_parcalari = text.split()
        ana_komut = komut_parcalari[0].lower().split("@")[0]

        if ana_komut in ["/id", "/myid", "/bilgi"]:
            telegramMesajGonder(
                chat_id,
                f"👤 <b>Telegram Kullanıcı Bilginiz:</b>\n"
                f"🆔 <b>Kullanıcı ID:</b> <code>{user_id}</code>\n\n"
                f"💡 <i>Botu kullanabilmek için bu ID numarasını yöneticiye iletiniz.</i>"
            )
            return

        if not yetkili_mi(user_id):
            telegramMesajGonder(
                chat_id,
                f"⛔ <b>Erişim Reddedildi!</b>\n"
                f"Bu bot sadece yetkili şirket yöneticilerine özeldir.\n"
                f"Kullanıcı ID'niz: <code>{user_id}</code>"
            )
            return

        if ana_komut in ["/start", "/menu", "/menü"]:
            telegramMesajGonder(chat_id, "👋 <b>CFO ve Finans Yönetim Botu</b>\nLütfen bir işlem seçin:\n\n👨💻 <i>Yazılım: @CRYPTOATAKAN © 2026</i>", menuKlavyesiOlustur(is_group))
        elif ana_komut in ["/rehber", "/komutlar", "/yardim", "/yardım"]:
            telegramMesajGonder(chat_id, rehber_metni())
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
            islemi_analiz_bildirimiyle_yap(chat_id, tumGruplarRaporu_impl)
        elif ana_komut in ["/masraf", "/gider"]:
            islemi_analiz_bildirimiyle_yap(chat_id, masrafRaporuUret_impl)
        elif ana_komut == "/canlikur":
            islemi_analiz_bildirimiyle_yap(chat_id, canliKurSorgula_impl)
        elif ana_komut == "/kur":
            islemi_analiz_bildirimiyle_yap(chat_id, kurRaporuUret_impl)
        elif ana_komut == "/iban":
            islemi_analiz_bildirimiyle_yap(chat_id, ibanListesiGetir_impl)
        elif ana_komut == "/hesap":
            islemi_analiz_bildirimiyle_yap(chat_id, hesapMakinesi_impl, text)
        elif ana_komut in ["/çeviri", "/ceviri"]:
            islemi_analiz_bildirimiyle_yap(chat_id, metinCevir_impl, text)
        elif ana_komut == "/yenigun":
            metin, klavye = yenigun_baslat_mesaji()
            telegramMesajGonder(chat_id, metin, klavye)
        elif ana_komut == "/kasa":
            islemi_analiz_bildirimiyle_yap(chat_id, hucreyeVeriYaz_impl, text, 4, "Kasa Ekleme", 1)
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
        elif ana_komut == "/gerial":
            def gerial_impl():
                if not app_state["SON_ISLEM"]:
                    return "Hafıza Boş: Geri alınacak işlem yok."
                last = app_state["SON_ISLEM"]
                sh = get_spreadsheet()
                sayfa = sh.worksheet(last["sayfa"])
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

# --- MAIN LOOP (LONG POLLING) ---
if __name__ == "__main__":
    threading.Thread(target=run_dashboard_server, daemon=True).start()
    print("CFO Bot & Canlı Dashboard Başlatıldı (7/24 Kesintisiz)...")
    
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset}&timeout=25"
            res = http_get_json(url)
            if res.get("ok"):
                for upd in res.get("result", []):
                    offset = upd["update_id"] + 1
                    process_telegram_update(upd)
        except Exception as e:
            time.sleep(2)
