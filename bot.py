import os
import re
import io
import json
import logging
import datetime
import threading
from typing import Optional, Dict, Any, List

import requests
import gspread
from google.oauth2.service_account import Credentials

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
)

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CFO_BOT")

# --- AYARLAR & SABİTLER ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8629756462:AAHSn66-SVOZzWp_UrBj36bHjF1hpts5bco")
KURUCU_ID = int(os.environ.get("KURUCU_ID", "8395730761"))
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "1Gim_-YSb_TtODclXiZ0hnx2WDsc-RCW9CD51LeVNOaI")
WEB_APP_URL = os.environ.get("WEB_APP_URL", "")
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

def get_gspread_client():
    # 1. Environment variable kontrolü
    json_env = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if json_env and json_env.strip():
        try:
            info = json.loads(json_env.strip())
            creds = Credentials.from_service_account_info(info, scopes=SCOPES)
            return gspread.authorize(creds)
        except Exception as e:
            logger.error(f"GOOGLE_SERVICE_ACCOUNT_JSON okunamadı: {e}")

    # 2. Dosya yolları kontrolü
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
                logger.error(f"{path} okunamadı: {e}")

    raise FileNotFoundError("Google Service Account anahtarı bulunamadı! Lütfen Environment değişkenlerine GOOGLE_SERVICE_ACCOUNT_JSON ekleyin.")

def get_spreadsheet():
    gc = get_gspread_client()
    return gc.open_by_key(SPREADSHEET_ID)

def bugununTarihiniAl():
    now = datetime.datetime.now()
    return now.strftime("%d.%m.%Y")

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
        logger.error(f"Log hatası: {e}")

def yetkili_mi(user_id: int) -> bool:
    if user_id == KURUCU_ID: return True
    return user_id in app_state["EK_ADMINLER"]

def menuKlavyesiOlustur(isGroup: bool):
    panel_button = (
        InlineKeyboardButton("🌐 Canlı CFO Paneli (Tarayıcıda Aç)", url=WEB_APP_URL)
        if (isGroup or not WEB_APP_URL) else
        InlineKeyboardButton("🌐 Canlı CFO Paneli (Mini-App)", web_app=WebAppInfo(url=WEB_APP_URL))
    )
    keyboard = [
        [panel_button],
        [InlineKeyboardButton("📊 Tüm Gruplar", callback_data="rapor_tumu")],
        [InlineKeyboardButton("📉 Masraf Raporu", callback_data="rapor_masraf")],
        [InlineKeyboardButton("💼 Hızlı Finans Özeti", callback_data="rapor_ozet")],
        [InlineKeyboardButton("🎯 Ciro Hedefi İbresi", callback_data="menu_hedef")]
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
                        keyboard.append([InlineKeyboardButton(f"{emoji} {gAd}", callback_data=f"rapor_{gAd}")])
    except:
        pass
    keyboard.append([InlineKeyboardButton("🛠️ Komut Rehberi", callback_data="rehber")])
    return InlineKeyboardMarkup(keyboard)

def rehber_metni():
    return (
        "📚 <b>SİSTEM KOMUT REHBERİ</b>\n━━━━━━━━━━━━━━━━━━━\n\n"
        "🏢 <b>KASA VE OPERASYON</b>\n"
        "<code>/kasa TİGER 1500</code> : Kasaya para ekler.\n"
        "<code>/kasasil TİGER 500</code> : Kasadan siler.\n"
        "<code>/odeme TİGER 1000</code> : Ödenen tutarı girer.\n"
        "<code>/odemesil TİGER 200</code> : Ödenen tutardan siler.\n"
        "<code>/masrafekle 500 Yemek</code> : Günlük masraf işler.\n"
        "<code>/masrafsil 500 Yemek</code> : Masraf siler.\n"
        "<code>/gerial</code> : En son işlemi geri alır.\n\n"
        "📊 <b>GÜNLÜK DÖNGÜ VE RAPORLAR</b>\n"
        "<code>/yenigun</code> : 🌅 Yeni gün sayfasını açar, devirleri koruyarak aktarır.\n"
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
        "<code>/notlar</code> : Son notları listeler.\n\n"
        "🛡️ <b>CEO & SİSTEM YÖNETİMİ</b>\n"
        "<code>/yedekle</code> : 📦 Tüm Excel verilerini .xlsx olarak gönderir.\n"
        "<code>/kilit</code> : Acil durumda veri girişini tamamen kilitler.\n"
        "<code>/kilitac</code> : Sistem kilidini kaldırır.\n"
        "<code>/panel</code> : Canlı CFO Web Dashboard linkini verir.\n\n"
        "💡 <i>Tüm işlemler arka planda güvenlik protokolüyle işlenmektedir.</i>"
    )

def hucreyeVeriYaz_impl(komut: str, sutun_idx: int, isim: str, carp: int):
    p = komut.split(" ")
    if len(p) < 3: raise ValueError("Eksik komut. Örnek: /kasa TİGER 1500")
    tutar = float(p[-1].replace(",", "."))
    grup = trKarakterCoz(" ".join(p[1:-1]))
    
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
    raise ValueError(f"Tabloda '{grup}' adlı grup bulunamadı.")

def masrafVerisiYaz_impl(komut: str, isim: str, carp: int):
    p = komut.split(" ")
    if len(p) < 3: raise ValueError("Eksik komut. Örnek: /masrafekle 500 Yemek")
    tutar = float(p[-1].replace(",", "."))
    masraf = trKarakterCoz(" ".join(p[1:-1]))
    
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
            return f"✅ <b>{isim} Başarılı!</b>\n📉 Masraf: {masraf}\nİşlem: {rakamFormatla(abs(tutar))} TL\n\n<i>Hatalı işlem mi? /gerial yazabilirsiniz.</i>"
            
    # Yeni masraf ekle
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
    return f"✅ <b>Yeni Masraf Oluşturuldu!</b>\n📉 Masraf: {masraf}\nTutar: {rakamFormatla(abs(tutar))} TL\n\n<i>Hatalı işlem mi? /gerial yazabilirsiniz.</i>"

def hizliOzetUret_impl():
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
        f"💡 <i>Tüm grupların anlık toplamıdır.</i>"
    )

def masrafRaporuUret_impl():
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

def kurRaporuUret_impl():
    yanit = "📊 <b>GÜNCEL KURLAR</b>\n\n"
    try:
        r = requests.get("https://data-api.binance.vision/api/v3/ticker/24hr?symbol=USDTTRY", timeout=5).json()
        yanit += f"🟡 <b>BİNANCE USDT/TRY</b>\n💵 Anlık Kur: {float(r['lastPrice']):.2f} ₺\n🔺 24s Yüksek: {float(r['highPrice']):.2f} ₺\n🔻 24s Düşük: {float(r['lowPrice']):.2f} ₺\n\n"
    except: yanit += "🟡 <b>BİNANCE USDT/TRY</b>\n⚠️ Veri çekilemedi.\n\n"
    try:
        r = requests.get("https://www.paribu.com/ticker", timeout=5).json()["USDT_TL"]
        yanit += f"🔵 <b>PARİBU USDT/TRY</b>\n💵 Anlık Kur: {float(r['last']):.2f} ₺\n🔺 24s Yüksek: {float(r['high24hr']):.2f} ₺\n🔻 24s Düşük: {float(r['low24hr']):.2f} ₺\n\n"
    except: yanit += "🔵 <b>PARİBU USDT/TRY</b>\n⚠️ Veri çekilemedi.\n\n"
    try:
        r = requests.get("https://api.btcturk.com/api/v2/ticker?pairSymbol=USDT_TRY", timeout=5).json()["data"][0]
        yanit += f"🟢 <b>BTCTÜRK USDT/TRY</b>\n💵 Anlık Kur: {float(r['last']):.2f} ₺\n🔺 24s Yüksek: {float(r['high']):.2f} ₺\n🔻 24s Düşük: {float(r['low']):.2f} ₺\n\n"
    except: yanit += "🟢 <b>BTCTÜRK USDT/TRY</b>\n⚠️ Veri çekilemedi.\n\n"
    return yanit.strip()

def canliKurSorgula_impl():
    try:
        b_usdt = requests.get("https://data-api.binance.vision/api/v3/ticker/price?symbol=USDTTRY", timeout=5).json()
        b_btc = requests.get("https://data-api.binance.vision/api/v3/ticker/price?symbol=BTCUSDT", timeout=5).json()
        b_eth = requests.get("https://data-api.binance.vision/api/v3/ticker/price?symbol=ETHUSDT", timeout=5).json()
        fiat = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5).json()["rates"]
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

def hesapMakinesi_impl(orijinalMetin: str):
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
        return f"⚠️ <b>Grup Bulunamadı:</b> Excel'de <code>{arananGrup}</code> bulunamadı."
        
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

def ibanListesiGetir_impl():
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

def metinCevir_impl(gelenMetin: str):
    cevrilecek = re.sub(r'^/(?:çeviri|ceviri)(?:@\w+)?\s*', '', gelenMetin, flags=re.IGNORECASE).strip()
    if not cevrilecek:
        return "⚠️ Lütfen çevrilmesini istediğiniz metni yazın.\nÖrnek: <code>/çeviri Merhaba</code>"
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": "auto", "tl": "tr", "dt": "t", "q": cevrilecek}
        res = requests.get(url, params=params, timeout=5).json()
        turkce = "".join([x[0] for x in res[0] if x[0]])
        son_ceviri = turkce
        etiket = "🌍 Yabancı Dil ➔ 🇹🇷 Türkçe"
        if turkce.lower() == cevrilecek.lower():
            params["tl"] = "en"
            res_en = requests.get(url, params=params, timeout=5).json()
            son_ceviri = "".join([x[0] for x in res_en[0] if x[0]])
            etiket = "🇹🇷 Türkçe ➔ 🇺🇸 İngilizce"
        return f"🌐 <b>YAPAY ZEKA ÇEVİRİSİ</b>\n━━━━━━━━━━━━━━━━━━━\n\n📝 <b>Orijinal Metin:</b>\n<i>{cevrilecek}</i>\n\n🎯 <b>{etiket}:</b>\n<code>{son_ceviri}</code>"
    except Exception as e:
        return f"❌ <b>Çeviri Hatası:</b> {e}"

# --- TELEGRAM HANDLERS ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not yetkili_mi(user_id):
        await update.message.reply_text("⛔ <b>Erişim Reddedildi!</b>\nSistem seni taradı... VIP yetkisi gereklidir.", parse_mode=ParseMode.HTML)
        return
    is_group = update.effective_chat.type in ["group", "supergroup"]
    await update.message.reply_text(
        "👋 <b>CFO ve Finans Yönetim Botu</b>\nLütfen bir işlem seçin:\n\n👨💻 <i>Yazılım: @CRYPTOATAKAN © 2026</i>",
        reply_markup=menuKlavyesiOlustur(is_group),
        parse_mode=ParseMode.HTML
    )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    
    if not yetkili_mi(user_id):
        await query.message.reply_text("⛔ <b>Erişim Reddedildi!</b>", parse_mode=ParseMode.HTML)
        return
        
    try:
        if data == "rehber":
            await query.message.reply_text(rehber_metni(), parse_mode=ParseMode.HTML)
        elif data == "rapor_ozet":
            await query.message.reply_text(hizliOzetUret_impl(), parse_mode=ParseMode.HTML)
        elif data == "rapor_masraf":
            await query.message.reply_text(masrafRaporuUret_impl(), parse_mode=ParseMode.HTML)
        elif data == "rapor_tumu":
            await query.message.reply_text(hizliOzetUret_impl(), parse_mode=ParseMode.HTML)
        elif data.startswith("rapor_"):
            grup = data.replace("rapor_", "")
            # Tek grup raporu
            sh = get_spreadsheet()
            sayfa = sh.worksheet(bugununTarihiniAl())
            veriler = sayfa.get_all_values()
            found = False
            for row in veriler[1:]:
                if len(row) >= 2 and trKarakterCoz(row[1]) == trKarakterCoz(grup):
                    vals = [guvenliSayi(x) for x in row[1:7]]
                    while len(vals) < 6: vals.append(0.0)
                    devir, kasa, odenen, kom, kalan = vals[1], vals[2], vals[3], vals[4], vals[5]
                    msg = (
                        f"📊 <b>[ {row[1]} ] GÜNCEL KASA ANALİZİ</b>\n━━━━━━━━━━━━\n"
                        f"📅 Tarih: {bugununTarihiniAl()}\n━━━━━━━━━━━━\n"
                        f"🔄 Önceki Devir: {paraFormatla(abs(devir))} ₺\n"
                        f"💰 Eklenen Kasa: {paraFormatla(abs(kasa))} ₺\n"
                        f"💸 Yapılan Ödeme: {paraFormatla(abs(odenen))} ₺\n"
                        f"✂️ Kesinti/Masraf: {paraFormatla(abs(kom))} ₺\n━━━━━━━━━━━━\n"
                        f"🏦 <b>NET KALAN TL: {paraFormatla(abs(kalan))} ₺</b>"
                    )
                    await query.message.reply_text(msg, parse_mode=ParseMode.HTML)
                    found = True
                    break
            if not found:
                await query.message.reply_text(f"⚠️ {grup} grubu için veri bulunamadı.", parse_mode=ParseMode.HTML)
    except Exception as e:
        await query.message.reply_text(f"❌ <b>Hata:</b> {e}", parse_mode=ParseMode.HTML)

async def generic_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text: return
    text = msg.text.strip()
    text_lower = text.lower()
    user_id = msg.from_user.id
    
    if not yetkili_mi(user_id):
        await msg.reply_text("⛔ <b>Erişim Reddedildi!</b>", parse_mode=ParseMode.HTML)
        return
        
    try:
        if text_lower in ["/start", "/menu", "/menü"] or text_lower.startswith("/start@") or text_lower.startswith("/menu@"):
            await start_command(update, context)
        elif text_lower in ["/rehber", "/komutlar", "/yardim", "/yardım"] or text_lower.startswith("/rehber@"):
            await msg.reply_text(rehber_metni(), parse_mode=ParseMode.HTML)
        elif text_lower == "/ozet" or text_lower.startswith("/ozet@"):
            await msg.reply_text(hizliOzetUret_impl(), parse_mode=ParseMode.HTML)
        elif text_lower == "/canlikur" or text_lower.startswith("/canlikur@"):
            await msg.reply_text(canliKurSorgula_impl(), parse_mode=ParseMode.HTML)
        elif text_lower == "/kur" or text_lower.startswith("/kur@"):
            await msg.reply_text(kurRaporuUret_impl(), parse_mode=ParseMode.HTML)
        elif text_lower == "/iban" or text_lower.startswith("/iban@"):
            await msg.reply_text(ibanListesiGetir_impl(), parse_mode=ParseMode.HTML)
        elif text_lower.startswith("/hesap"):
            await msg.reply_text(hesapMakinesi_impl(text), parse_mode=ParseMode.HTML)
        elif text_lower.startswith("/çeviri") or text_lower.startswith("/ceviri"):
            await msg.reply_text(metinCevir_impl(text), parse_mode=ParseMode.HTML)
        elif text_lower.startswith("/kasa "):
            await msg.reply_text(hucreyeVeriYaz_impl(text, 4, "Güncel Kasa Ekleme", 1), parse_mode=ParseMode.HTML)
        elif text_lower.startswith("/kasasil "):
            await msg.reply_text(hucreyeVeriYaz_impl(text, 4, "Güncel Kasa Silme", -1), parse_mode=ParseMode.HTML)
        elif text_lower.startswith("/odeme "):
            await msg.reply_text(hucreyeVeriYaz_impl(text, 5, "Ödenen Ekleme", 1), parse_mode=ParseMode.HTML)
        elif text_lower.startswith("/odemesil "):
            await msg.reply_text(hucreyeVeriYaz_impl(text, 5, "Ödenen Silme", -1), parse_mode=ParseMode.HTML)
        elif text_lower.startswith("/devir "):
            await msg.reply_text(hucreyeVeriYaz_impl(text, 3, "Devir Ekleme", 1), parse_mode=ParseMode.HTML)
        elif text_lower.startswith("/devirsil "):
            await msg.reply_text(hucreyeVeriYaz_impl(text, 3, "Devir Silme", -1), parse_mode=ParseMode.HTML)
        elif text_lower.startswith("/masrafekle "):
            await msg.reply_text(masrafVerisiYaz_impl(text, "Masraf Ekleme", 1), parse_mode=ParseMode.HTML)
        elif text_lower.startswith("/masrafsil "):
            await msg.reply_text(masrafVerisiYaz_impl(text, "Masraf Silme", -1), parse_mode=ParseMode.HTML)
        elif text_lower == "/gerial":
            if not app_state["SON_ISLEM"]:
                await msg.reply_text("Hafıza Boş: Geri alınacak işlem yok.", parse_mode=ParseMode.HTML)
            else:
                last = app_state["SON_ISLEM"]
                sh = get_spreadsheet()
                sayfa = sh.worksheet(last["sayfa"])
                sayfa.update_cell(last["satir"], last["sutun"], last["eskiDeger"])
                app_state["SON_ISLEM"] = None
                sistemeLogYaz("İptal Edilen İşlem", f"{last['grupAdi']} ({last['islemTuru']})")
                await msg.reply_text(f"⏪ <b>ZAMAN GERİYE SARILDI!</b>\nHedef: <b>{last['grupAdi']}</b>\nEski haline döndürüldü.", parse_mode=ParseMode.HTML)
        elif text_lower.startswith("/not ") and not text_lower.startswith("/notlar"):
            not_metni = text[5:].strip()
            sh = get_spreadsheet()
            try: not_sayfasi = sh.worksheet("NOTLAR")
            except: not_sayfasi = sh.add_worksheet(title="NOTLAR", rows=500, cols=3)
            now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
            not_sayfasi.append_row([now_str, not_metni])
            await msg.reply_text(f"📓 <b>NOT KAYDEDİLDİ</b>\n🕒 {now_str}\n📝 <i>{not_metni}</i>", parse_mode=ParseMode.HTML)
        elif text_lower == "/notlar" or text_lower.startswith("/notlar@"):
            sh = get_spreadsheet()
            not_sayfasi = sh.worksheet("NOTLAR")
            rows = not_sayfasi.get_all_values()
            if len(rows) < 1:
                await msg.reply_text("📭 Not defteri boş.", parse_mode=ParseMode.HTML)
            else:
                last_10 = rows[-10:]
                out = "📓 <b>ŞİRKET HAFIZASI (SON NOTLAR)</b>\n━━━━━━━━━━━━━━━━━━━\n\n"
                for r in reversed(last_10):
                    out += f"📌 <b>{r[0] if len(r)>0 else ''}</b>\n<code>{r[1] if len(r)>1 else ''}</code>\n\n"
                await msg.reply_text(out, parse_mode=ParseMode.HTML)
    except Exception as err:
        logger.error(f"Komut hatası: {err}")
        await msg.reply_text(f"❌ <b>Hata:</b> {err}", parse_mode=ParseMode.HTML)

# --- FASTAPI WEB APPLICATION (CFO LIVE DASHBOARD) ---
fastapi_app = FastAPI()

@fastapi_app.get("/api/dashboard")
async def get_dashboard_api():
    try:
        sh = get_spreadsheet()
        sayfa = sh.worksheet(bugununTarihiniAl())
        veriler = sayfa.get_all_values()
        gruplar = []
        toplamDevir = toplamKasa = toplamOdenen = toplamCiro = toplamKalan = 0.0
        for row in veriler[1:]:
            if len(row) >= 2:
                ad = row[1].strip().upper()
                if ad and ad != "*" and "GENEL TOPLAM" not in ad:
                    vals = [guvenliSayi(x) for x in row[1:7]]
                    while len(vals) < 6: vals.append(0.0)
                    devir, kasa, odenen, kom, kalan = vals[1], vals[2], vals[3], vals[4], vals[5]
                    if any(abs(x) > 0.01 for x in [devir, kasa, odenen, kalan]):
                        gruplar.append({"ad": row[1], "devir": devir, "kasa": kasa, "odenen": odenen, "kalan": kalan, "komisyon": kom})
                        toplamDevir += devir
                        toplamKasa += kasa
                        toplamOdenen += odenen
                        toplamCiro += kom
                        toplamKalan += kalan
        return {"tarih": bugununTarihiniAl(), "devir": toplamDevir, "kasa": toplamKasa, "odenen": toplamOdenen, "kalan": toplamKalan, "ciro": toplamCiro, "gruplar": gruplar}
    except Exception as e:
        return {"error": str(e)}

@fastapi_app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    return "<h3>Canlı CFO Paneli Çalışıyor!</h3>"

def start_fastapi():
    uvicorn.run(fastapi_app, host="0.0.0.0", port=8080, log_level="warning")

if __name__ == "__main__":
    # Arka planda web dashboard'u aç
    threading.Thread(target=start_fastapi, daemon=True).start()
    
    # Telegram Botunu ana döngüde %100 kesintisiz dinle
    print("CFO Botu Telegram Polling Başlatılıyor...")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("menu", start_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.ALL, generic_message_handler))
    app.run_polling(drop_pending_updates=False)
