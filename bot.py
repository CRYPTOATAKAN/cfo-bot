import os
import re
import io
import json
import time
import datetime
import threading
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional, Dict, Any, List, Tuple

import gspread
from google.oauth2.service_account import Credentials

# --- AYARLAR & SABİTLER ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8629756462:AAHSn66-SVOZzWp_UrBj36bHjF1hpts5bco")
KURUCU_ID = int(os.environ.get("KURUCU_ID", "8395730761"))
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "1Gim_-YSb_TtODclXiZ0hnx2WDsc-RCW9CD51LeVNOaI")
WEB_APP_URL = os.environ.get("WEB_APP_URL", "https://site--cfo-bot-servis--drx8qyvbw8cw.code.run")
LOG_SAYFASI = "Guvenlik_Log"

app_state = {
    "EK_ADMINLER": [],
    "SISTEM_KILIDI": "PASIF",
    "CIRO_HEDEFI": None,
    "SON_ISLEM": None,
    "LOG_HAFTASI": None,
    "GRUP_BAGLANTILARI": {}
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
    # 1. Environment variable
    json_env = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if json_env and json_env.strip():
        try:
            info = json.loads(json_env.strip())
            creds = Credentials.from_service_account_info(info, scopes=SCOPES)
            return gspread.authorize(creds)
        except Exception as e:
            print(f"GOOGLE_SERVICE_ACCOUNT_JSON okunamadı: {e}")

    # 2. Dosya yolları
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

def bugununTarihiniAl():
    return datetime.datetime.now().strftime("%d.%m.%Y")

def dununTarihiniAl():
    dun = datetime.datetime.now() - datetime.timedelta(days=1)
    return dun.strftime("%d.%m.%Y")

def trKarakterCoz(metin: str) -> str:
    if not metin: return ""
    tr_map = str.maketrans("iıüöçşğ", "IIÜÖÇŞĞ")
    return metin.strip().translate(tr_map).upper()

def grupEmojisiBul(grupAdi: str) -> str:
    temiz = trKarakterCoz(grupAdi)
    if "TİGER" in temiz or "TIGER" in temiz: return "🐅"
    if "GENEL TOPLAM" in temiz: return "🏆"
    if "MASRAF" in temiz or "GİDER" in temiz: return "📉"
    if "KARGO" in temiz: return "📦"
    return "🔹"

def rakamFormatla(sayi) -> str:
    try:
        val = int(round(float(sayi)))
        return f"{val:,}".replace(",", ".")
    except:
        return str(sayi)

def paraFormatla(deger) -> str:
    try:
        val = float(deger)
        tam = int(val)
        tam_str = f"{tam:,}".replace(",", ".")
        ondalik = f"{val:.2f}".split(".")[1]
        return f"{tam_str},{ondalik}"
    except:
        return "0,00"

def guvenliSayi(deger) -> float:
    if deger is None or deger == "": return 0.0
    if isinstance(deger, (int, float)): return float(deger)
    metin = str(deger).strip()
    if metin in ["", "-"]: return 0.0
    eksi_mi = "-" in metin
    temiz = re.sub(r'[^0-9,.]', '', metin)
    if "," in temiz and "." in temiz:
        temiz = temiz.replace(".", "").replace(",", ".")
    elif "," in temiz:
        temiz = temiz.replace(",", ".")
    try:
        sayi = float(temiz)
        return -sayi if eksi_mi else sayi
    except:
        return 0.0

def sistemeLogYaz(islemAdi: str, detay: str):
    try:
        sh = get_spreadsheet()
        try:
            logSayfasi = sh.worksheet(LOG_SAYFASI)
        except gspread.exceptions.WorksheetNotFound:
            logSayfasi = sh.add_worksheet(title=LOG_SAYFASI, rows=500, cols=5)
            logSayfasi.append_row(["Tarih/Saat", "İşlem Türü", "İşlem Detayı"])
        tarihSaat = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
        logSayfasi.append_row([tarihSaat, islemAdi, detay])
    except Exception as e:
        print(f"Log hatası: {e}")

def yetkili_mi(user_id: int) -> bool:
    if user_id == KURUCU_ID: return True
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
        sayfa = sh.worksheet(bugununTarihiniAl())
        tum_satirlar = sayfa.get_all_values()
        eklenen = set()
        for r in tum_satirlar[1:]:
            if len(r) >= 2:
                gAd = r[1].strip()
                if gAd and gAd != "*" and "GENEL TOPLAM" not in gAd.upper():
                    uAd = gAd.upper()
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
        "📚 <b>SİSTEM KOMUT REHBERİ</b>\n━━━━━━━━━━━━━━━━━━━\n\n"
        "🏢 <b>KASA VE OPERASYON</b>\n"
        "<code>/kasa TİGER 1500</code> : Kasaya para ekler.\n"
        "<code>/kasasil TİGER 500</code> : Kasadan siler.\n"
        "<code>/odeme TİGER 1000</code> : Ödenen tutarı girer.\n"
        "<code>/odemesil TİGER 200</code> : Ödenen tutardan siler.\n"
        "<code>/devir TİGER 5000</code> : Devir bakiyesi ekler.\n"
        "<code>/devirsil TİGER 1000</code> : Devirden siler.\n"
        "<code>/masrafekle Yemek 500</code> : Günlük masraf işler.\n"
        "<code>/masrafsil Yemek 200</code> : Masraf siler.\n"
        "<code>/masraf</code> : Günlük masraf listesini döker.\n"
        "<code>/gerial</code> : En son işlemi geri alır.\n\n"
        "📊 <b>GÜNLÜK DÖNGÜ VE RAPORLAR</b>\n"
        "<code>/yenigun</code> : 🌅 Yeni gün sayfasını açar, devirleri aktarır.\n"
        "<code>/rapor</code> : Tüm grupların güncel durum raporunu çeker.\n"
        "<code>/ozet</code> : Kasa, Masraf ve Ödenen hızlı özetini sunar.\n"
        "<code>/log</code> veya <code>/son5</code> : Yapılan son işlemleri listeler.\n\n"
        "🌍 <b>EKSTRA ARAÇLAR</b>\n"
        "<code>/canlikur</code> : 🌍 Dünya borsalarını ve kripto kurlarını anlık getirir.\n"
        "<code>/kur</code> : 🟡 Anlık USDT kurlarını listeler.\n"
        "<code>/iban</code> : 🏦 Kullanımdaki ve boşta olan İBAN'ları listeler.\n"
        "<code>/çeviri [Metin]</code> : 🌐 Otomatik çeviri yapar.\n"
        "<code>/hesap TİGER 5 34.50</code> : Tether / Kasa hesap makinesi.\n"
        "<code>/not [Metin]</code> : Şirket ajandasına not ekler.\n"
        "<code>/notlar</code> : Son notları listeler.\n"
        "<code>/panel</code> : Canlı CFO Web Dashboard linkini verir.\n\n"
        "🛡️ <b>CEO & SİSTEM YÖNETİMİ</b>\n"
        "<code>/kilit</code> : Acil durumda veri girişini tamamen kilitler.\n"
        "<code>/kilitac</code> : Sistem kilidini kaldırır.\n"
        "<code>/adminekle [ID]</code> : Yeni yönetici ekler.\n"
        "<code>/adminsil [ID]</code> : Yöneticiyi siler.\n"
        "<code>/adminler</code> : Yetkili yöneticileri listeler.\n\n"
        "💡 <i>Tüm işlemler arka planda güvenlik protokolüyle işlenmektedir.</i>"
    )

def parse_grup_ve_tutar(parametreler: List[str]) -> Tuple[str, float]:
    """Parametrelerin sırası ne olursa olsun grup adı ve tutarı akıllıca ayrıştırır."""
    if len(parametreler) < 2:
        raise ValueError("Eksik bilgi! Örnek kullanım: /kasa TİGER 1500")
    
    # 1. Durum: Son parametre tutar mı? (/kasa TİGER 1500)
    try:
        tutar = float(parametreler[-1].replace(",", "."))
        grup = " ".join(parametreler[:-1]).strip()
        return grup, tutar
    except ValueError:
        pass
        
    # 2. Durum: İlk parametre tutar mı? (/kasa 1500 TİGER)
    try:
        tutar = float(parametreler[0].replace(",", "."))
        grup = " ".join(parametreler[1:]).strip()
        return grup, tutar
    except ValueError:
        raise ValueError("Lütfen geçerli bir sayısal tutar girin! Örnek: /kasa TİGER 1500")

def hucreyeVeriYaz_impl(komut_metni: str, sutun_idx: int, isim: str, carp: int) -> str:
    parcalar = komut_metni.strip().split()[1:]
    grup_ham, tutar = parse_grup_ve_tutar(parcalar)
    grup = trKarakterCoz(grup_ham)
    
    sh = get_spreadsheet()
    sayfa = sh.worksheet(bugununTarihiniAl())
    tum_veriler = sayfa.get_all_values()
    
    for i, row in enumerate(tum_veriler[1:], start=2):
        if len(row) >= 2 and trKarakterCoz(row[1]) == grup:
            mevcut_val = guvenliSayi(row[sutun_idx - 1]) if len(row) >= sutun_idx else 0.0
            yeni_val = mevcut_val + (tutar * carp)
            sayfa.update_cell(i, sutun_idx, yeni_val)
            
            app_state["SON_ISLEM"] = {
                "sayfa": bugununTarihiniAl(), "satir": i, "sutun": sutun_idx,
                "eskiDeger": mevcut_val, "grupAdi": row[1], "islemTuru": isim
            }
            sistemeLogYaz(isim, f"{row[1].upper()} | {rakamFormatla(abs(tutar))} TL")
            
            row_vals = [guvenliSayi(x) for x in row[1:7]]
            while len(row_vals) < 6: row_vals.append(0.0)
            row_vals[sutun_idx - 2] = yeni_val
            dDevir, dKasa, dOdenen, dKomisyon, dKalan = row_vals[1], row_vals[2], row_vals[3], row_vals[4], row_vals[5]
            
            return (
                f"✅ <b>{isim} Başarılı!</b>\n━━━━━━━━━━━\n"
                f"{grupEmojisiBul(row[1])} <b>{row[1].upper()}</b>\n"
                f"İşlem: {rakamFormatla(abs(tutar))} TL\n\n"
                f"📊 <b>Güncel Durum:</b>\n"
                f"🔄 Devir: {paraFormatla(abs(dDevir))} ₺\n"
                f"💰 Kasa: {paraFormatla(abs(dKasa))} ₺\n"
                f"💸 Ödenen: {paraFormatla(abs(dOdenen))} ₺\n"
                f"✂️ Komisyon: {paraFormatla(abs(dKomisyon))} ₺\n"
                f"🏦 Kalan: <b>{paraFormatla(abs(dKalan))} ₺</b>\n\n"
                f"<i>Hatalı işlem mi? /gerial yazabilirsiniz.</i>"
            )
    raise ValueError(f"Tabloda '<b>{grup_ham}</b>' adlı grup bulunamadı.")

def masrafVerisiYaz_impl(komut_metni: str, isim: str, carp: int) -> str:
    parcalar = komut_metni.strip().split()[1:]
    masraf_ham, tutar = parse_grup_ve_tutar(parcalar)
    masraf = trKarakterCoz(masraf_ham)
    
    sh = get_spreadsheet()
    sayfa = sh.worksheet(bugununTarihiniAl())
    tum_veriler = sayfa.get_all_values()
    
    for i, row in enumerate(tum_veriler[1:], start=2):
        col_i = row[8] if len(row) > 8 else ""
        col_j = row[9] if len(row) > 9 else ""
        if trKarakterCoz(col_i) == masraf:
            mevcut = guvenliSayi(col_j)
            yeni = mevcut + (tutar * carp)
            sayfa.update_cell(i, 10, yeni)
            app_state["SON_ISLEM"] = {"sayfa": bugununTarihiniAl(), "satir": i, "sutun": 10, "eskiDeger": mevcut, "grupAdi": masraf, "islemTuru": isim}
            sistemeLogYaz(isim, f"{masraf} | {rakamFormatla(abs(tutar))} TL")
            return f"✅ <b>{isim} Başarılı!</b>\n📉 Masraf: <b>{masraf}</b>\nİşlem: {rakamFormatla(abs(tutar))} TL\n\n<i>Hatalı işlem mi? /gerial yazabilirsiniz.</i>"
            
    # Yeni masraf satırı
    bos_satir = len(tum_veriler) + 1
    for i, row in enumerate(tum_veriler[1:], start=2):
        col_i = row[8] if len(row) > 8 else ""
        if not col_i.strip():
            bos_satir = i
            break
    sayfa.update_cell(bos_satir, 9, masraf)
    sayfa.update_cell(bos_satir, 10, tutar)
    app_state["SON_ISLEM"] = {"sayfa": bugununTarihiniAl(), "satir": bos_satir, "sutun": 10, "eskiDeger": 0, "grupAdi": masraf, "islemTuru": "Yeni Masraf Ekleme"}
    sistemeLogYaz("Yeni Masraf Ekleme", f"{masraf} | {rakamFormatla(abs(tutar))} TL")
    return f"✅ <b>Yeni Masraf Oluşturuldu!</b>\n📉 Masraf: <b>{masraf}</b>\nTutar: {rakamFormatla(abs(tutar))} TL\n\n<i>Hatalı işlem mi? /gerial yazabilirsiniz.</i>"

def hizliOzetUret_impl() -> str:
    sh = get_spreadsheet()
    sayfa = sh.worksheet(bugununTarihiniAl())
    veriler = sayfa.get_all_values()
    toplamDevir = toplamKasa = toplamOdenen = toplamKomisyon = toplamKalan = 0.0
    aktifGrupSayisi = 0
    
    for row in veriler[1:]:
        if len(row) >= 2:
            ad = row[1].strip().upper()
            if ad and ad != "*" and "GENEL TOPLAM" not in ad:
                vals = [guvenliSayi(x) for x in row[1:7]]
                while len(vals) < 6: vals.append(0.0)
                devir, kasa, odenen, kom, kalan = vals[1], vals[2], vals[3], vals[4], vals[5]
                toplamDevir += devir
                toplamKasa += kasa
                toplamOdenen += odenen
                toplamKomisyon += kom
                toplamKalan += kalan
                if any(abs(x) > 0.01 for x in [devir, kasa, odenen, kalan]):
                    aktifGrupSayisi += 1
                    
    saat = datetime.datetime.now().strftime("%H:%M")
    return (
        f"📊 <b>GÜNLÜK FİNANS BİLANÇOSU</b>\n━━━━━━━━━━━\n"
        f"📅 <b>Tarih:</b> {bugununTarihiniAl()} | ⏰ <b>Saat:</b> {saat}\n"
        f"🏢 <b>Aktif Grup Sayısı:</b> {aktifGrupSayisi}\n━━━━━━━━━━━\n\n"
        f"🔄 Toplam Devir: {paraFormatla(abs(toplamDevir))} ₺\n"
        f"💰 Eklenen Kasa: {paraFormatla(abs(toplamKasa))} ₺\n"
        f"💸 Toplam Ödeme: {paraFormatla(abs(toplamOdenen))} ₺\n"
        f"✂️ Toplam Kesinti: {paraFormatla(abs(toplamKomisyon))} ₺\n\n"
        f"━━━━━━━━━━━\n"
        f"🏦 <b>NET KALAN KASA: {paraFormatla(abs(toplamKalan))} ₺</b>\n━━━━━━━━━━━\n"
        f"💡 <i>Tüm grupların anlık genel toplamıdır.</i>"
    )

def tumGruplarRaporu_impl() -> str:
    sh = get_spreadsheet()
    sayfa = sh.worksheet(bugununTarihiniAl())
    veriler = sayfa.get_all_values()
    
    mesaj = f"📊 <b>{bugununTarihiniAl()} GÜNCEL KASA RAPORU</b>\n━━━━━━━━━━━━━━━━━━━\n\n"
    bulunan = 0
    toplamKalan = 0.0
    
    for row in veriler[1:]:
        if len(row) >= 2:
            grup = row[1].strip()
            if grup and grup != "*" and "GENEL TOPLAM" not in grup.upper():
                vals = [guvenliSayi(x) for x in row[1:7]]
                while len(vals) < 6: vals.append(0.0)
                devir, kasa, odenen, kom, kalan = vals[1], vals[2], vals[3], vals[4], vals[5]
                if any(abs(x) > 0.01 for x in [devir, kasa, odenen, kalan]):
                    bulunan += 1
                    toplamKalan += kalan
                    emoji = grupEmojisiBul(grup)
                    mesaj += (
                        f"{emoji} <b>{grup.upper()}</b>\n"
                        f"🔄 Devir: {paraFormatla(abs(devir))} ₺ | 💰 Kasa: {paraFormatla(abs(kasa))} ₺\n"
                        f"💸 Ödenen: {paraFormatla(abs(odenen))} ₺ | 🏦 <b>Kalan: {paraFormatla(abs(kalan))} ₺</b>\n"
                        f"───────────────────\n"
                    )
    if bulunan == 0:
        return "📭 <b>Henüz işlem görmüş aktif bir grup bulunmuyor.</b>"
        
    mesaj += f"\n🏦 <b>GENEL TOPLAM NET KALAN: {paraFormatla(abs(toplamKalan))} ₺</b>"
    return mesaj

def masrafRaporuUret_impl() -> str:
    sh = get_spreadsheet()
    sayfa = sh.worksheet(bugununTarihiniAl())
    veriler = sayfa.get_all_values()
    masraflar = []
    toplam = 0.0
    for row in veriler[1:]:
        if len(row) >= 10:
            ad = row[8].strip()
            if ad and "GENEL TOPLAM" not in ad.upper() and ad != "-":
                fiyat = guvenliSayi(row[9])
                if abs(fiyat) > 0:
                    toplam += abs(fiyat)
                    masraflar.append({"ad": ad, "fiyat": abs(fiyat)})
    if not masraflar:
        return "📭 <b>Bugün için kaydedilmiş bir masraf bulunmuyor.</b>"
    masraflar.sort(key=lambda x: x["fiyat"], reverse=True)
    mesaj = f"📉 <b>{bugununTarihiniAl()} GÜNLÜK GİDER TABLOSU</b>\n━━━━━━━━━━━━━━━━━━━\n\n"
    for m in masraflar:
        mesaj += f"▪️ <b>{m['ad']}</b> ➔ {rakamFormatla(m['fiyat'])} ₺\n"
    mesaj += f"\n━━━━━━━━━━━━━━━━━━━\n📋 Toplam Kalem: <b>{len(masraflar)} Adet</b>\n📊 <b>TOPLAM GİDER: {rakamFormatla(toplam)} ₺</b>"
    return mesaj

def kurRaporuUret_impl() -> str:
    yanit = "📊 <b>GÜNCEL KRİPTO KURLARI</b>\n\n"
    try:
        r = http_get_json("https://data-api.binance.vision/api/v3/ticker/24hr?symbol=USDTTRY")
        yanit += f"🟡 <b>BİNANCE USDT/TRY</b>\n💵 Anlık Kur: {float(r['lastPrice']):.2f} ₺\n🔺 24s Yüksek: {float(r['highPrice']):.2f} ₺\n🔻 24s Düşük: {float(r['lowPrice']):.2f} ₺\n\n"
    except: yanit += "🟡 <b>BİNANCE USDT/TRY</b>\n⚠️ Veri çekilemedi.\n\n"
    try:
        r = http_get_json("https://www.paribu.com/ticker")["USDT_TL"]
        yanit += f"🔵 <b>PARİBU USDT/TRY</b>\n💵 Anlık Kur: {float(r['last']):.2f} ₺\n🔺 24s Yüksek: {float(r['high24hr']):.2f} ₺\n🔻 24s Düşük: {float(r['low24hr']):.2f} ₺\n\n"
    except: yanit += "🔵 <b>PARİBU USDT/TRY</b>\n⚠️ Veri çekilemedi.\n\n"
    try:
        r = http_get_json("https://api.btcturk.com/api/v2/ticker?pairSymbol=USDT_TRY")["data"][0]
        yanit += f"🟢 <b>BTCTÜRK USDT/TRY</b>\n💵 Anlık Kur: {float(r['last']):.2f} ₺\n🔺 24s Yüksek: {float(r['high']):.2f} ₺\n🔻 24s Düşük: {float(r['low']):.2f} ₺\n\n"
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
            "🌍 <b>CANLI PİYASA & DÜNYA KURLARI</b>\n━━━━━━━━━━━━━━━━━━━\n\n"
            "🪙 <b>Kripto Paralar (Binance)</b>\n"
            f"🇹🇷 <b>USDT / TRY:</b> <code>{float(b_usdt['price']):.2f} ₺</code>\n"
            f"🔶 <b>BTC / USDT:</b> <code>{float(b_btc['price']):,.0f} $</code>\n"
            f"🔷 <b>ETH / USDT:</b> <code>{float(b_eth['price']):.2f} $</code>\n\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "💵 <b>Dünya Para Birimleri</b>\n"
            f"🇺🇸 <b>Dolar (USD):</b> <code>{try_rate:.2f} ₺</code>\n"
            f"🇪🇺 <b>Euro (EUR):</b> <code>{(try_rate / fiat.get('EUR', 1)):.2f} ₺</code>\n"
            f"🇬🇧 <b>Sterlin (GBP):</b> <code>{(try_rate / fiat.get('GBP', 1)):.2f} ₺</code>\n"
            f"🇨🇭 <b>İsviçre Frangı (CHF):</b> <code>{(try_rate / fiat.get('CHF', 1)):.2f} ₺</code>\n"
            f"🇨🇦 <b>Kanada Doları (CAD):</b> <code>{(try_rate / fiat.get('CAD', 1)):.2f} ₺</code>\n"
            f"🇦🇺 <b>Avustralya Dol. (AUD):</b> <code>{(try_rate / fiat.get('AUD', 1)):.2f} ₺</code>\n"
            f"🇯🇵 <b>Japon Yeni (JPY):</b> <code>{(try_rate / fiat.get('JPY', 1)):.2f} ₺</code>\n"
            f"🇸🇦 <b>Suudi Riyali (SAR):</b> <code>{(try_rate / fiat.get('SAR', 1)):.2f} ₺</code>\n"
            f"🇷🇺 <b>Rus Rublesi (RUB):</b> <code>{(try_rate / fiat.get('RUB', 1)):.2f} ₺</code>\n\n"
            f"<i>⏱ Son Güncelleme: {datetime.datetime.now().strftime('%H:%M:%S')}</i>"
        )
    except Exception as e:
        return f"❌ <b>API Hatası:</b> {e}"

def hesapMakinesi_impl(orijinalMetin: str) -> str:
    args = orijinalMetin.strip().split()
    if len(args) < 4:
        return "⚠️ <b>Hatalı Kullanım!</b>\nFormat: <code>/hesap GRUPADI ORAN KUR</code>\nÖrnek: <code>/hesap TİGER 5 34.50</code>"
    kurStr = args.pop()
    komisyonStr = args.pop()
    arananGrup = " ".join(args[1:]).strip()
    
    kur = float(kurStr.replace(",", "."))
    komisyonOrani = float(komisyonStr.replace(",", "."))
    
    sh = get_spreadsheet()
    sayfa = sh.worksheet(bugununTarihiniAl())
    veriler = sayfa.get_all_values()
    
    grupBulundu = False
    devirBorc = 0.0
    guncelKasa = 0.0
    gercekGrupAdi = arananGrup
    arananTemiz = trKarakterCoz(arananGrup)
    
    for row in veriler[1:]:
        if len(row) >= 2 and trKarakterCoz(row[1]) == arananTemiz:
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
    duzUsdt = int(usdtKarsiligi)
    
    islemZamani = datetime.datetime.now().strftime("%d.%m.%Y | %H:%M")
    mesaj = (
        f"🧮 <b>HESAP KESİM RAPORU</b>\n━━━━━━━━━━━━━━━━━━━\n"
        f"🏢 <b>Grup:</b> {gercekGrupAdi}\n"
        f"🕒 <b>Zaman:</b> {islemZamani}\n\n"
    )
    if devirBorc != 0:
        mesaj += f"⚠️ <b>DEVİR / BORÇ HATIRLATMASI</b> ⚠️\nGeçmişten Kalan: <b>{paraFormatla(devirBorc)} ₺</b>\n━━━━━━━━━━━━━━━━━━━\n\n"
    mesaj += (
        f"💵 <b>Güncel Kasa:</b> {paraFormatla(guncelKasa)} ₺\n"
        f"📉 <b>Komisyon (% {komisyonOrani}):</b> {paraFormatla(komisyonKesintisi)} ₺\n"
        f"✅ <b>Net Bakiye (TL):</b> {paraFormatla(netKasaTl)} ₺\n\n"
        f"💱 <b>İşlem Kuru:</b> {kur}\n"
        f"🪙 <b>Tether Karşılığı:</b> <code>{rakamFormatla(duzUsdt)} USDT</code>"
    )
    return mesaj

def ibanListesiGetir_impl() -> str:
    sh = get_spreadsheet()
    sayfa = sh.worksheet(bugununTarihiniAl())
    veriler = sayfa.get_all_values()
    bosta, dolu = [], []
    for row in veriler[1:]:
        if len(row) > 11 and row[11].strip():
            ib1 = row[11].strip()
            not1 = row[14].strip() if len(row) > 14 else ""
            if not not1: bosta.append(f"• <code>{ib1}</code>")
            else: dolu.append(f"👤 <b>{not1}:</b> <code>{ib1}</code>")
        if len(row) > 15 and row[15].strip():
            ib2 = row[15].strip()
            not2 = row[17].strip() if len(row) > 17 else ""
            if not not2: bosta.append(f"• <code>{ib2}</code>")
            else: dolu.append(f"👤 <b>{not2}:</b> <code>{ib2}</code>")
    mesaj = "🏦 <b>İBAN LİSTESİ</b> <i>(Kopyalamak için dokunun)</i>\n\n"
    mesaj += "🟢 <b>BOŞTAKİLER</b>\n" + ("\n".join(bosta) if bosta else "<i>Boşta İBAN yok.</i>") + "\n\n"
    mesaj += "🔴 <b>KULLANIMDAKİLER</b>\n" + ("\n".join(dolu) if dolu else "<i>Kullanımda İBAN yok.</i>")
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
        return f"🌐 <b>YAPAY ZEKA ÇEVİRİSİ</b>\n━━━━━━━━━━━━━━━━━━━\n\n📝 <b>Orijinal Metin:</b>\n<i>{cevrilecek}</i>\n\n🎯 <b>{etiket}:</b>\n<code>{son_ceviri}</code>"
    except Exception as e:
        return f"❌ <b>Çeviri Hatası:</b> {e}"

def yenigun_baslat_mesaji():
    klavye = {
        "inline_keyboard": [
            [{"text": "🔄 Masrafları Temizle & Yeni Güne Geç", "callback_data": "yenigun_onay_sil"}],
            [{"text": "📋 Masrafları Koru & Yeni Güne Geç", "callback_data": "yenigun_onay_tut"}],
            [{"text": "❌ İptal Et", "callback_data": "yenigun_iptal"}]
        ]
    }
    return (
        "🌅 <b>YENİ GÜN DEVİR İŞLEMİ</b>\n━━━━━━━━━━━━━━━━━━━\n\n"
        "Bugünün sayfası açılacak, dün kalan bakiyeler **Devir** sütununa otomatik aktarılacaktır.\n\n"
        "Lütfen masraf tercihinizi seçin:",
        klavye
    )

def yenigun_gerceklestir_impl(masraflari_sil: bool) -> str:
    sh = get_spreadsheet()
    yeniTarih = bugununTarihiniAl()
    
    # Sayfa zaten var mı?
    try:
        sh.worksheet(yeniTarih)
        return f"⚠️ <b>{yeniTarih}</b> tarihli sayfa zaten mevcut! Yeniden oluşturulamaz."
    except gspread.exceptions.WorksheetNotFound:
        pass
        
    # En son sayfayı bul
    tum_sayfalar = sh.worksheets()
    kaynak_sayfa = None
    for ws in tum_sayfalar:
        if ws.title not in [LOG_SAYFASI, "NOTLAR", "YEDEK"]:
            kaynak_sayfa = ws
            break
            
    if not kaynak_sayfa:
        return "❌ Kopyalanacak şablon sayfa bulunamadı!"
        
    # Sayfayı kopyala
    yeni_sayfa = kaynak_sayfa.duplicate(new_sheet_name=yeniTarih)
    veriler = yeni_sayfa.get_all_values()
    
    # Devirleri aktar (Dünün Kalanı -> Yeni Günün Deviri)
    for r_idx, row in enumerate(veriler[1:], start=2):
        if r_idx > 43: break # Formül koruması
        if len(row) >= 7:
            grup = row[1].strip()
            if grup and grup != "*" and "GENEL TOPLAM" not in grup.upper():
                dunun_kalani = guvenliSayi(row[6]) # G sütunu (Kalan)
                yeni_sayfa.update_cell(r_idx, 3, dunun_kalani) # C sütunu (Devir)
                yeni_sayfa.update_cell(r_idx, 4, 0) # D sütunu (Kasa sıfırla)
                yeni_sayfa.update_cell(r_idx, 5, 0) # E sütunu (Ödenen sıfırla)
                
    if masraflari_sil:
        # Masrafları temizle
        for r_idx in range(2, 40):
            yeni_sayfa.update_cell(r_idx, 9, "")
            yeni_sayfa.update_cell(r_idx, 10, "")
            
    sistemeLogYaz("Yeni Gün Geçişi", f"Yeni gün sayfası ({yeniTarih}) açıldı.")
    return f"🌅 <b>{yeniTarih} GÜNÜ BAŞARIYLA AÇILDI!</b>\n\n🔄 Dünün tüm kalan bakiyeleri Devir sütununa aktarıldı.\n💰 Kasa ve Ödenen alanları sıfırlandı.\n\nİyi çalışmalar dileriz! 🚀"

# --- YARDIMCI: VERİ ANALİZİ BİLDİRİMİ İLE ÇALIŞTIRICI ---
def islemi_analiz_bildirimiyle_yap(chat_id: int, islem_fn, *args):
    """Kullanıcı komut yazdığında anında bildirim verir, işlem bitince kendini siler."""
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
    # 1. Buton Tıklamaları (Callback Query)
    if "callback_query" in update:
        cq = update["callback_query"]
        data = cq.get("data", "")
        chat_id = cq["message"]["chat"]["id"]
        user_id = cq["from"]["id"]
        
        telegram_api("answerCallbackQuery", {"callback_query_id": cq["id"]})
        
        if not yetkili_mi(user_id):
            telegramMesajGonder(chat_id, "⛔ <b>Erişim Reddedildi!</b>")
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
            def tek_grup_rapor(grup_adi):
                sh = get_spreadsheet()
                sayfa = sh.worksheet(bugununTarihiniAl())
                veriler = sayfa.get_all_values()
                for row in veriler[1:]:
                    if len(row) >= 2 and trKarakterCoz(row[1]) == trKarakterCoz(grup_adi):
                        vals = [guvenliSayi(x) for x in row[1:7]]
                        while len(vals) < 6: vals.append(0.0)
                        devir, kasa, odenen, kom, kalan = vals[1], vals[2], vals[3], vals[4], vals[5]
                        return (
                            f"📊 <b>[ {row[1].upper()} ] GÜNCEL KASA ANALİZİ</b>\n━━━━━━━━━━━━\n"
                            f"📅 Tarih: {bugununTarihiniAl()}\n━━━━━━━━━━━━\n"
                            f"🔄 Önceki Devir: {paraFormatla(abs(devir))} ₺\n"
                            f"💰 Eklenen Kasa: {paraFormatla(abs(kasa))} ₺\n"
                            f"💸 Yapılan Ödeme: {paraFormatla(abs(odenen))} ₺\n"
                            f"✂️ Kesinti/Masraf: {paraFormatla(abs(kom))} ₺\n━━━━━━━━━━━━\n"
                            f"🏦 <b>NET KALAN TL: {paraFormatla(abs(kalan))} ₺</b>"
                        )
                return f"⚠️ <b>{grup_adi}</b> grubu için veri bulunamadı."
            islemi_analiz_bildirimiyle_yap(chat_id, tek_grup_rapor, grup)
        return

    # 2. Normal Mesajlar
    if "message" in update and "text" in update["message"]:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        user_id = msg["from"]["id"]
        text = msg["text"].strip()
        text_lower = text.lower()
        is_group = chat_id < 0

        if not yetkili_mi(user_id):
            telegramMesajGonder(chat_id, "⛔ <b>Erişim Reddedildi!</b>")
            return

        if text_lower in ["/start", "/menu", "/menü"] or text_lower.startswith("/start@") or text_lower.startswith("/menu@"):
            telegramMesajGonder(chat_id, "👋 <b>CFO ve Finans Yönetim Botu</b>\nLütfen bir işlem seçin:\n\n👨💻 <i>Yazılım: @CRYPTOATAKAN © 2026</i>", menuKlavyesiOlustur(is_group))
        elif text_lower in ["/rehber", "/komutlar", "/yardim", "/yardım"] or text_lower.startswith("/rehber@"):
            telegramMesajGonder(chat_id, rehber_metni())
        elif text_lower == "/panel" or text_lower.startswith("/panel@"):
            telegramMesajGonder(chat_id, f"🌐 <b>Canlı CFO Web Paneli:</b>\n{WEB_APP_URL}")
        elif text_lower == "/ozet" or text_lower.startswith("/ozet@"):
            islemi_analiz_bildirimiyle_yap(chat_id, hizliOzetUret_impl)
        elif text_lower == "/rapor" or text_lower.startswith("/rapor@"):
            islemi_analiz_bildirimiyle_yap(chat_id, tumGruplarRaporu_impl)
        elif text_lower in ["/masraf", "/gider"] or text_lower.startswith("/masraf@"):
            islemi_analiz_bildirimiyle_yap(chat_id, masrafRaporuUret_impl)
        elif text_lower == "/canlikur" or text_lower.startswith("/canlikur@"):
            islemi_analiz_bildirimiyle_yap(chat_id, canliKurSorgula_impl)
        elif text_lower == "/kur" or text_lower.startswith("/kur@"):
            islemi_analiz_bildirimiyle_yap(chat_id, kurRaporuUret_impl)
        elif text_lower == "/iban" or text_lower.startswith("/iban@"):
            islemi_analiz_bildirimiyle_yap(chat_id, ibanListesiGetir_impl)
        elif text_lower.startswith("/hesap"):
            islemi_analiz_bildirimiyle_yap(chat_id, hesapMakinesi_impl, text)
        elif text_lower.startswith("/çeviri") or text_lower.startswith("/ceviri"):
            islemi_analiz_bildirimiyle_yap(chat_id, metinCevir_impl, text)
        elif text_lower == "/yenigun" or text_lower.startswith("/yenigun@"):
            metin, klavye = yenigun_baslat_mesaji()
            telegramMesajGonder(chat_id, metin, klavye)
        elif text_lower.startswith("/kasa "):
            islemi_analiz_bildirimiyle_yap(chat_id, hucreyeVeriYaz_impl, text, 4, "Kasa Ekleme", 1)
        elif text_lower.startswith("/kasasil "):
            islemi_analiz_bildirimiyle_yap(chat_id, hucreyeVeriYaz_impl, text, 4, "Kasa Silme", -1)
        elif text_lower.startswith("/odeme "):
            islemi_analiz_bildirimiyle_yap(chat_id, hucreyeVeriYaz_impl, text, 5, "Ödenen Ekleme", 1)
        elif text_lower.startswith("/odemesil "):
            islemi_analiz_bildirimiyle_yap(chat_id, hucreyeVeriYaz_impl, text, 5, "Ödenen Silme", -1)
        elif text_lower.startswith("/devir "):
            islemi_analiz_bildirimiyle_yap(chat_id, hucreyeVeriYaz_impl, text, 3, "Devir Ekleme", 1)
        elif text_lower.startswith("/devirsil "):
            islemi_analiz_bildirimiyle_yap(chat_id, hucreyeVeriYaz_impl, text, 3, "Devir Silme", -1)
        elif text_lower.startswith("/masrafekle "):
            islemi_analiz_bildirimiyle_yap(chat_id, masrafVerisiYaz_impl, text, "Masraf Ekleme", 1)
        elif text_lower.startswith("/masrafsil "):
            islemi_analiz_bildirimiyle_yap(chat_id, masrafVerisiYaz_impl, text, "Masraf Silme", -1)
        elif text_lower == "/gerial":
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
        elif text_lower.startswith("/not ") and not text_lower.startswith("/notlar"):
            def not_ekle_impl():
                not_metni = text[5:].strip()
                sh = get_spreadsheet()
                try: not_sayfasi = sh.worksheet("NOTLAR")
                except: not_sayfasi = sh.add_worksheet(title="NOTLAR", rows=500, cols=3)
                now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
                not_sayfasi.append_row([now_str, not_metni])
                return f"📓 <b>NOT KAYDEDİLDİ</b>\n🕒 {now_str}\n📝 <i>{not_metni}</i>"
            islemi_analiz_bildirimiyle_yap(chat_id, not_ekle_impl)
        elif text_lower == "/notlar" or text_lower.startswith("/notlar@"):
            def notlari_getir_impl():
                sh = get_spreadsheet()
                not_sayfasi = sh.worksheet("NOTLAR")
                rows = not_sayfasi.get_all_values()
                if len(rows) < 1:
                    return "📭 Not defteri boş."
                last_10 = rows[-10:]
                out = "📓 <b>ŞİRKET HAFIZASI (SON NOTLAR)</b>\n━━━━━━━━━━━━━━━━━━━\n\n"
                for r in reversed(last_10):
                    out += f"📌 <b>{r[0] if len(r)>0 else ''}</b>\n<code>{r[1] if len(r)>1 else ''}</code>\n\n"
                return out
            islemi_analiz_bildirimiyle_yap(chat_id, notlari_getir_impl)

# --- HEALTH SERVER ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<h1>CFO Canli Paneli 7/24 Aktif</h1>")
    def log_message(self, format, *args): pass

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

# --- MAIN LOOP (LONG POLLING) ---
if __name__ == "__main__":
    threading.Thread(target=run_health_server, daemon=True).start()
    print("CFO Bot Polling Başlatıldı (7/24 Kesintisiz)...")
    
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
