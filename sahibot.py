"""
Sahibinden ilan takip botu.

Kayitli arama linklerini belirli araliklarla tarar, daha once gorulmemis
ilanlari ve fiyati degisen ilanlari Telegram uzerinden bildirir.
"""

import os
import telebot
from telebot import types
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
from bs4 import BeautifulSoup
import threading
import time
import random
import datetime
import json
import ssl
import winreg
import statistics

ssl._create_default_https_context = ssl._create_unverified_context

ANA_DIZIN = os.path.dirname(os.path.abspath(__file__))
VERI_DOSYASI = os.path.join(ANA_DIZIN, "kullanici_verileri.json")
HESAP_DOSYASI = os.path.join(ANA_DIZIN, "hesap_bilgileri.json")


# ---------------------------------------------------------------
# AYARLAR
# ---------------------------------------------------------------
def env_dosyasini_yukle():
    """.env dosyasindaki KEY=value satirlarini ortam degiskenlerine aktarir."""
    yol = os.path.join(ANA_DIZIN, ".env")
    if not os.path.exists(yol):
        return

    with open(yol, "r", encoding="utf-8") as f:
        for satir in f:
            satir = satir.strip()
            if not satir or satir.startswith("#") or "=" not in satir:
                continue
            anahtar, deger = satir.split("=", 1)
            os.environ.setdefault(anahtar.strip(), deger.strip())


def id_listesi_oku(anahtar):
    """Virgulle ayrilmis chat id listesini ortam degiskeninden okur."""
    ham = os.getenv(anahtar, "")
    return [int(parca) for parca in ham.split(",") if parca.strip()]


env_dosyasini_yukle()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise SystemExit("TELEGRAM_TOKEN tanimli degil. .env.example dosyasini .env olarak kopyalayip doldurun.")

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Sadece bu chat id'ler botu kullanabilir
IZINLI_KULLANICILAR = id_listesi_oku("IZINLI_KULLANICILAR")

# Bu kullanicilar herkesin aramasini gorup iptal edebilir
ADMIN_KULLANICILAR = id_listesi_oku("ADMIN_KULLANICILAR")


# ---------------------------------------------------------------
# DURUM (bellekte tutulan veriler)
# ---------------------------------------------------------------
# {chat_id: {arama_adi: {url, gorulen_ilanlar, ilk_tarama, fiyatlar, ilan_fiyatlari, aktif}}}
aktif_kullanicilar = {}

# Cok adimli sohbet akislarinin ara durumu
gecici_link_bekleme = {}   # {chat_id: url} - isim sorulurken
gecici_url_duzenleme = {}  # {chat_id: arama_adi} - yeni link sorulurken
gecici_hesap_bekleme = {}  # {chat_id: {"eposta": ...}} - sifre sorulurken

# Telegram callback_data uzunluk siniri nedeniyle butonlara kisa id verilir
admin_iptal_haritasi = {}  # {kisa_id: (chat_id, arama_adi)}

# Sahibinden hesap bilgileri: {chat_id: {"eposta": ..., "sifre": ...}}
hesap_bilgileri = {}

# SMS kodu tarama thread'inde beklenir, Telegram thread'inde alinir
sms_event = {}
sms_kod_deposu = {}

# Ust uste kac turda dogrulama ekranina dusuldugunu sayar.
# Sayi arttikca tur arasi bekleme uzar, boylece siteye binen yuk azalir.
ust_uste_dogrulama_sayisi = 0


def zaman():
    return datetime.datetime.now().strftime("%H:%M:%S")


def yetki_kontrol(chat_id):
    return chat_id in IZINLI_KULLANICILAR


# ---------------------------------------------------------------
# ARAMA VERILERI
# ---------------------------------------------------------------
def verileri_kaydet():
    kaydedilecek_veri = {}
    for chat_id, gorevler in aktif_kullanicilar.items():
        kaydedilecek_veri[str(chat_id)] = {}
        for isim, data in gorevler.items():
            kaydedilecek_veri[str(chat_id)][isim] = {
                "url": data["url"],
                # set JSON'a yazilamadigi icin listeye cevriliyor
                "gorulen_ilanlar": list(data["gorulen_ilanlar"]),
                "ilk_tarama": data["ilk_tarama"],
                "fiyatlar": data.get("fiyatlar", []),
                "ilan_fiyatlari": data.get("ilan_fiyatlari", {}),
                "aktif": data.get("aktif", True)
            }

    with open(VERI_DOSYASI, "w", encoding="utf-8") as f:
        json.dump(kaydedilecek_veri, f, ensure_ascii=False, indent=4)


def verileri_yukle():
    global aktif_kullanicilar

    if not os.path.exists(VERI_DOSYASI):
        print(f"[{zaman()}] [BİLGİ] Veri dosyası yok, yeni veritabanı oluşturulacak.")
        aktif_kullanicilar = {}
        return

    with open(VERI_DOSYASI, "r", encoding="utf-8") as f:
        try:
            gecici_veri = json.load(f)
            for chat_id_str, gorevler in gecici_veri.items():
                chat_id = int(chat_id_str)
                aktif_kullanicilar[chat_id] = {}
                for isim, data in gorevler.items():
                    # get() varsayilanlari, eski surumle kaydedilmis dosyalarin
                    # yeni alanlar olmadan da sorunsuz yuklenmesini saglar
                    aktif_kullanicilar[chat_id][isim] = {
                        "url": data.get("url"),
                        "gorulen_ilanlar": set(data.get("gorulen_ilanlar", [])),
                        "ilk_tarama": data.get("ilk_tarama", True),
                        "fiyatlar": data.get("fiyatlar", []),
                        "ilan_fiyatlari": data.get("ilan_fiyatlari", {}),
                        "aktif": data.get("aktif", True)
                    }
            print(f"[{zaman()}] [SİSTEM] Veritabanı yüklendi. (Kayıtlı Kullanıcı: {len(aktif_kullanicilar)})")
        except Exception as e:
            print(f"[{zaman()}] [HATA] Veri okuma hatası: {e}. Temiz oturum başlatılıyor.")
            aktif_kullanicilar = {}


# ---------------------------------------------------------------
# HESAP BILGILERI
# ---------------------------------------------------------------
# Not: sifreler duz metin saklanir. Dosya .gitignore icinde, sadece
# kendi hesabiniz icin kullanin.
def hesap_verilerini_kaydet():
    with open(HESAP_DOSYASI, "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in hesap_bilgileri.items()}, f, ensure_ascii=False, indent=4)


def hesap_verilerini_yukle():
    global hesap_bilgileri

    if not os.path.exists(HESAP_DOSYASI):
        hesap_bilgileri = {}
        return

    try:
        with open(HESAP_DOSYASI, "r", encoding="utf-8") as f:
            ham = json.load(f)
            hesap_bilgileri = {int(k): v for k, v in ham.items()}
        print(f"[{zaman()}] [SİSTEM] Hesap bilgileri yüklendi. ({len(hesap_bilgileri)} kayıt)")
    except Exception as e:
        print(f"[{zaman()}] [HATA] Hesap verisi okunamadı: {e}")
        hesap_bilgileri = {}


# ---------------------------------------------------------------
# TARAYICI
# ---------------------------------------------------------------
def tarayici_olustur():
    # Onceki oturumdan kalan chrome surecleri profili kilitli birakabiliyor
    os.system("taskkill /F /IM chrome.exe /T >nul 2>&1")
    time.sleep(2)

    profil_yolu = os.path.join(os.getcwd(), "sahibinden_profil")

    kilit_dosyasi = os.path.join(profil_yolu, "SingletonLock")
    if os.path.exists(kilit_dosyasi):
        try:
            os.remove(kilit_dosyasi)
        except:
            pass

    opts = uc.ChromeOptions()
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-popup-blocking")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    # Ayni profil kullanilinca giris oturumu turlar arasinda korunur
    opts.add_argument(f"--user-data-dir={profil_yolu}")

    # Chrome'un kurulu oldugu yeri once kayit defterinden, bulunamazsa
    # bilinen klasorlerden bul
    dogru_yol = None
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as key:
                yol, _ = winreg.QueryValueEx(key, "")
                if yol and os.path.exists(yol):
                    dogru_yol = yol
                    break
        except WindowsError:
            continue

    if not dogru_yol:
        chrome_yollari = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
        for yol in chrome_yollari:
            if os.path.exists(yol):
                dogru_yol = yol
                break

    if dogru_yol:
        return uc.Chrome(options=opts, browser_executable_path=dogru_yol, version_main=150)

    try:
        return uc.Chrome(options=opts)
    except Exception:
        raise FileNotFoundError("Google Chrome kurulu değil veya tespit edilemedi.")


def elemani_bul(driver, secenekler, bekle=0):
    """
    Verilen (yontem, deger) ciftlerini sirayla dener, ilk buldugu elemani doner.
    Sahibinden zaman zaman form alanlarinin id/name degerlerini degistirdigi
    icin tek secici yerine liste kullaniliyor.
    """
    bitis = time.time() + bekle
    while True:
        for yontem, deger in secenekler:
            try:
                el = driver.find_element(yontem, deger)
                if el:
                    return el
            except NoSuchElementException:
                continue
        if time.time() >= bitis:
            return None
        time.sleep(0.5)


# ---------------------------------------------------------------
# TELEGRAM KOMUTLARI
# ---------------------------------------------------------------
@bot.message_handler(commands=['start'])
def start_komutu(message):
    chat_id = message.chat.id
    kullanici_adi = message.from_user.username

    print(f"\n[{zaman()}] [BİLGİ] Bota mesaj geldi! ID: {chat_id} | Kullanıcı: @{kullanici_adi}")

    if not yetki_kontrol(chat_id):
        print(f"[{zaman()}] [UYARI] İzinsiz giriş denemesi engellendi! (ID: {chat_id})")
        return

    if chat_id not in aktif_kullanicilar:
        aktif_kullanicilar[chat_id] = {}

    mesaj = (
        "Merhaba. Sahibinden botuna hoş geldiniz.\n\n"
        "Link Eklemek İçin: Sahibinden veya shbd.io linkini yapıştırın.\n"
        "Aramalarınızı Görmek İçin: /liste\n"
        "Arama Silmek İçin: /sil\n"
        "Arama Duraklat/Devam Ettir: /duraklat\n"
        "Arama Bağlantısını Değiştir: /duzenle\n\n"
        "Sahibinden Hesabınızı Bağlamak İçin: /hesap\n"
        "Hesap doğrulaması gerektiğinde SMS kodunu size soracağım."
    )
    if chat_id in ADMIN_KULLANICILAR:
        mesaj += "\n\n👑 Admin: /admin ile tüm kullanıcıların aramalarını görüp iptal edebilirsiniz."

    bot.send_message(chat_id, mesaj)


@bot.message_handler(commands=['liste'])
def liste_komutu(message):
    chat_id = message.chat.id
    if not yetki_kontrol(chat_id):
        return

    gorevler = aktif_kullanicilar.get(chat_id, {})
    if not gorevler:
        bot.send_message(chat_id, "Kayıtlı aramanız bulunmuyor.")
        return

    metin = "Kayıtlı Aramalarınız:\n\n"
    for isim, data in gorevler.items():
        durum_isareti = "🟢" if data.get("aktif", True) else "⏸️"
        metin += f"{durum_isareti} {isim}\n"
    bot.send_message(chat_id, metin)


@bot.message_handler(commands=['sil'])
def sil_komutu(message):
    chat_id = message.chat.id
    if not yetki_kontrol(chat_id):
        return

    gorevler = aktif_kullanicilar.get(chat_id, {})
    if not gorevler:
        bot.send_message(chat_id, "Silinecek kayıtlı aramanız bulunmuyor.")
        return

    butonlar = types.InlineKeyboardMarkup()
    for isim in gorevler.keys():
        butonlar.add(types.InlineKeyboardButton(text=f"Sil: {isim}", callback_data=f"sil_{isim}"))

    bot.send_message(chat_id, "Silmek istediğiniz aramayı seçin:", reply_markup=butonlar)


@bot.callback_query_handler(func=lambda call: call.data.startswith('sil_'))
def sil_callback(call):
    try:
        bot.answer_callback_query(call.id)
        chat_id = call.message.chat.id
        if not yetki_kontrol(chat_id):
            return

        isim = call.data[len('sil_'):]

        if chat_id in aktif_kullanicilar and isim in aktif_kullanicilar[chat_id]:
            del aktif_kullanicilar[chat_id][isim]
            verileri_kaydet()
            bot.edit_message_text(f"'{isim}' isimli arama başarıyla silindi.", chat_id, call.message.message_id)
            print(f"[{zaman()}] [BİLGİ] Görev silindi: '{isim}'")
        else:
            bot.edit_message_text("Arama bulunamadı veya daha önce silinmiş.", chat_id, call.message.message_id)

    except Exception as e:
        print(f"[{zaman()}] [HATA] Silme işlemi sırasında hata oluştu: {e}")


# ---- DURAKLAT / DEVAM ETTIR ----
# Silmekten farki: gorulen ilanlar ve fiyat gecmisi korunur, sadece taranmaz.
@bot.message_handler(commands=['duraklat'])
def duraklat_komutu(message):
    chat_id = message.chat.id
    if not yetki_kontrol(chat_id):
        return

    gorevler = aktif_kullanicilar.get(chat_id, {})
    if not gorevler:
        bot.send_message(chat_id, "Kayıtlı aramanız bulunmuyor.")
        return

    butonlar = types.InlineKeyboardMarkup()
    for isim, data in gorevler.items():
        aktif_mi = data.get("aktif", True)
        etiket = f"⏸️ Duraklat: {isim}" if aktif_mi else f"▶️ Devam Ettir: {isim}"
        butonlar.add(types.InlineKeyboardButton(text=etiket, callback_data=f"duraklat_{isim}"))

    bot.send_message(chat_id, "Durumunu değiştirmek istediğiniz aramayı seçin:", reply_markup=butonlar)


@bot.callback_query_handler(func=lambda call: call.data.startswith('duraklat_'))
def duraklat_callback(call):
    try:
        bot.answer_callback_query(call.id)
        chat_id = call.message.chat.id
        if not yetki_kontrol(chat_id):
            return

        isim = call.data[len('duraklat_'):]
        if chat_id in aktif_kullanicilar and isim in aktif_kullanicilar[chat_id]:
            mevcut = aktif_kullanicilar[chat_id][isim].get("aktif", True)
            aktif_kullanicilar[chat_id][isim]["aktif"] = not mevcut
            verileri_kaydet()
            yeni_durum = "⏸️ Duraklatıldı" if mevcut else "🟢 Aktif"
            bot.edit_message_text(f"'{isim}' durumu güncellendi: {yeni_durum}", chat_id, call.message.message_id)
            print(f"[{zaman()}] [BİLGİ] '{isim}' durumu değiştirildi: {yeni_durum} (chat_id: {chat_id})")
        else:
            bot.edit_message_text("Arama bulunamadı veya daha önce silinmiş.", chat_id, call.message.message_id)

    except Exception as e:
        print(f"[{zaman()}] [HATA] Duraklatma işlemi sırasında hata oluştu: {e}")


# ---- ADMIN PANELI ----
@bot.message_handler(commands=['admin'])
def admin_komutu(message):
    chat_id = message.chat.id
    if chat_id not in ADMIN_KULLANICILAR:
        return

    tum_gorevler = [(hedef_chat_id, isim, data) for hedef_chat_id, gorevler in aktif_kullanicilar.items()
                     for isim, data in gorevler.items()]

    if not tum_gorevler:
        bot.send_message(chat_id, "Kayıtlı hiç arama yok.")
        return

    admin_iptal_haritasi.clear()
    metin = "📋 Tüm Kayıtlı Aramalar:\n\n"
    butonlar = types.InlineKeyboardMarkup()

    for sayac, (hedef_chat_id, isim, data) in enumerate(tum_gorevler, start=1):
        kisa_id = str(sayac)
        admin_iptal_haritasi[kisa_id] = (hedef_chat_id, isim)
        metin += f"{sayac}. [{hedef_chat_id}] {isim}\n{data.get('url', '')}\n\n"
        butonlar.add(types.InlineKeyboardButton(
            text=f"❌ {sayac}. {isim} ({hedef_chat_id})",
            callback_data=f"adminsil_{kisa_id}"
        ))

    bot.send_message(chat_id, metin)
    bot.send_message(chat_id, "İptal etmek istediğiniz aramayı seçin:", reply_markup=butonlar)


@bot.callback_query_handler(func=lambda call: call.data.startswith('adminsil_'))
def admin_sil_callback(call):
    try:
        bot.answer_callback_query(call.id)
        chat_id = call.message.chat.id
        if chat_id not in ADMIN_KULLANICILAR:
            return

        kisa_id = call.data[len('adminsil_'):]
        hedef = admin_iptal_haritasi.pop(kisa_id, None)

        # Bot yeniden baslamissa veya liste yenilenmisse harita bosalmis olur
        if not hedef:
            bot.edit_message_text("Bu kayıt artık geçerli değil, /admin ile listeyi yenileyin.",
                                   chat_id, call.message.message_id)
            return

        hedef_chat_id, isim = hedef
        if hedef_chat_id in aktif_kullanicilar and isim in aktif_kullanicilar[hedef_chat_id]:
            del aktif_kullanicilar[hedef_chat_id][isim]
            verileri_kaydet()
            bot.edit_message_text(f"'{isim}' ({hedef_chat_id}) iptal edildi.", chat_id, call.message.message_id)
            print(f"[{zaman()}] [ADMIN] '{isim}' (chat_id: {hedef_chat_id}) admin tarafından iptal edildi.")
        else:
            bot.edit_message_text("Arama bulunamadı veya daha önce silinmiş.", chat_id, call.message.message_id)

    except Exception as e:
        print(f"[{zaman()}] [HATA] Admin silme işlemi sırasında hata oluştu: {e}")


# ---- HESAP KAYDI ----
@bot.message_handler(commands=['hesap'])
def hesap_komutu(message):
    chat_id = message.chat.id
    if not yetki_kontrol(chat_id):
        return

    msg = bot.send_message(
        chat_id,
        "Sahibinden hesabınızın e-posta veya telefon numarasını yazın:\n\n"
        "Not: Bu bilgi sunucuda düz metin olarak saklanacak. Sadece kendi hesabınız için kullanın."
    )
    bot.register_next_step_handler(msg, hesap_eposta_al)


def hesap_eposta_al(message):
    chat_id = message.chat.id
    if not yetki_kontrol(chat_id):
        return

    eposta = message.text.strip()
    gecici_hesap_bekleme[chat_id] = {"eposta": eposta}
    msg = bot.send_message(chat_id, "Şimdi şifrenizi yazın:")
    bot.register_next_step_handler(msg, hesap_sifre_al)


def hesap_sifre_al(message):
    chat_id = message.chat.id
    if not yetki_kontrol(chat_id):
        return

    sifre = message.text.strip()
    bekleyen = gecici_hesap_bekleme.get(chat_id)
    if not bekleyen:
        bot.send_message(chat_id, "Bir sorun oluştu, /hesap komutunu tekrar deneyin.")
        return

    hesap_bilgileri[chat_id] = {"eposta": bekleyen["eposta"], "sifre": sifre}
    hesap_verilerini_kaydet()
    del gecici_hesap_bekleme[chat_id]

    # Sifreyi iceren mesaji sohbet gecmisinden temizle
    try:
        bot.delete_message(chat_id, message.message_id)
    except:
        pass

    bot.send_message(chat_id, "Hesap bilgileriniz kaydedildi. Doğrulama gerektiğinde SMS kodunu sizden isteyeceğim.")
    print(f"[{zaman()}] [BİLGİ] Hesap bilgisi kaydedildi. (chat_id: {chat_id})")


@bot.message_handler(commands=['hesapsil'])
def hesapsil_komutu(message):
    chat_id = message.chat.id
    if not yetki_kontrol(chat_id):
        return

    if chat_id in hesap_bilgileri:
        del hesap_bilgileri[chat_id]
        hesap_verilerini_kaydet()
        bot.send_message(chat_id, "Hesap bilgileriniz silindi.")
    else:
        bot.send_message(chat_id, "Kayıtlı hesap bilginiz yok.")


# ---- YENI ARAMA EKLEME ----
@bot.message_handler(func=lambda message: ("sahibinden.com" in message.text or "shbd.io" in message.text) and message.text.startswith("http"))
def url_yakala(message):
    chat_id = message.chat.id
    if not yetki_kontrol(chat_id):
        return

    url = message.text.strip()
    gecici_link_bekleme[chat_id] = url
    msg = bot.send_message(chat_id, "Bağlantı algılandı! Bu arama filtresine kısa bir isim verin (Örn: Kadıköy 2+1):")
    bot.register_next_step_handler(msg, isim_kaydet)


def isim_kaydet(message):
    chat_id = message.chat.id
    if not yetki_kontrol(chat_id):
        return

    isim = message.text.strip()
    url = gecici_link_bekleme.get(chat_id)

    if not url:
        return

    if chat_id not in aktif_kullanicilar:
        aktif_kullanicilar[chat_id] = {}

    # ilk_tarama=True: ilk turda mevcut ilanlar sessizce kaydedilir,
    # bildirim yagmuru olmaz
    aktif_kullanicilar[chat_id][isim] = {
        "url": url,
        "gorulen_ilanlar": set(),
        "ilk_tarama": True,
        "fiyatlar": [],
        "ilan_fiyatlari": {},
        "aktif": True
    }

    verileri_kaydet()
    del gecici_link_bekleme[chat_id]

    print(f"[{zaman()}] [BİLGİ] Yeni görev eklendi: '{isim}'")
    bot.send_message(chat_id, f"'{isim}' isimli arama kaydedildi! İlk tarama başlatılıyor.")


# ---- ARAMA LINKINI DEGISTIRME ----
@bot.message_handler(commands=['duzenle'])
def duzenle_komutu(message):
    chat_id = message.chat.id
    if not yetki_kontrol(chat_id):
        return

    gorevler = aktif_kullanicilar.get(chat_id, {})
    if not gorevler:
        bot.send_message(chat_id, "Bağlantısını değiştirebileceğiniz kayıtlı aramanız bulunmuyor.")
        return

    butonlar = types.InlineKeyboardMarkup()
    for isim in gorevler.keys():
        butonlar.add(types.InlineKeyboardButton(text=f"Bağlantıyı Değiştir: {isim}", callback_data=f"duzenlesec_{isim}"))

    bot.send_message(chat_id, "Bağlantısını değiştirmek istediğiniz aramayı seçin:", reply_markup=butonlar)


@bot.callback_query_handler(func=lambda call: call.data.startswith('duzenlesec_'))
def duzenle_sec_callback(call):
    try:
        bot.answer_callback_query(call.id)
        chat_id = call.message.chat.id
        if not yetki_kontrol(chat_id):
            return

        isim = call.data[len('duzenlesec_'):]
        if chat_id in aktif_kullanicilar and isim in aktif_kullanicilar[chat_id]:
            gecici_url_duzenleme[chat_id] = isim
            bot.edit_message_text(f"'{isim}' için yeni bağlantıyı yapıştırın:", chat_id, call.message.message_id)
            bot.register_next_step_handler(call.message, yeni_url_al)
        else:
            bot.edit_message_text("Arama bulunamadı veya daha önce silinmiş.", chat_id, call.message.message_id)

    except Exception as e:
        print(f"[{zaman()}] [HATA] URL düzenleme seçimi sırasında hata oluştu: {e}")


def yeni_url_al(message):
    chat_id = message.chat.id
    if not yetki_kontrol(chat_id):
        return

    isim = gecici_url_duzenleme.pop(chat_id, None)
    if not isim:
        bot.send_message(chat_id, "Bir sorun oluştu, /duzenle komutunu tekrar deneyin.")
        return

    yeni_url = message.text.strip()
    gecerli = ("sahibinden.com" in yeni_url or "shbd.io" in yeni_url) and yeni_url.startswith("http")
    if not gecerli:
        bot.send_message(chat_id, "Geçersiz bağlantı, işlem iptal edildi. /duzenle ile tekrar deneyin.")
        return

    if chat_id not in aktif_kullanicilar or isim not in aktif_kullanicilar[chat_id]:
        bot.send_message(chat_id, "Arama bulunamadı veya daha önce silinmiş.")
        return

    # Link degisince sonuc kumesi de degisir. Eski gecmis tutulursa yeni
    # linkteki tum ilanlar "yeni" sanilip bildirim yagar; bu yuzden sifirlanir.
    gorev = aktif_kullanicilar[chat_id][isim]
    gorev["url"] = yeni_url
    gorev["gorulen_ilanlar"] = set()
    gorev["ilan_fiyatlari"] = {}
    gorev["fiyatlar"] = []
    gorev["ilk_tarama"] = True
    verileri_kaydet()

    bot.send_message(
        chat_id,
        f"'{isim}' için bağlantı güncellendi. Yeni bir temel tarama (baseline) yapılacak, "
        f"mevcut ilanlar bildirim olarak gelmeyecek."
    )
    print(f"[{zaman()}] [BİLGİ] '{isim}' için URL güncellendi (chat_id: {chat_id}).")


# ---------------------------------------------------------------
# GIRIS VE SMS DOGRULAMA
# ---------------------------------------------------------------
def sms_kodu_al(message):
    chat_id = message.chat.id
    if not yetki_kontrol(chat_id):
        return

    kod = message.text.strip()
    sms_kod_deposu[chat_id] = kod

    try:
        bot.delete_message(chat_id, message.message_id)
    except:
        pass

    # Bekleyen tarama thread'ini uyandir
    if chat_id in sms_event:
        sms_event[chat_id].set()

    bot.send_message(chat_id, "Kod alındı, giriş tamamlanıyor...")


def sms_kodu_iste(chat_id, zaman_asimi=180):
    """
    Kullanicidan SMS kodunu ister ve kod gelene kadar tarama thread'ini
    bekletir. Kodu ya da zaman asiminda None doner.
    """
    sms_event[chat_id] = threading.Event()
    sms_kod_deposu.pop(chat_id, None)

    msg = bot.send_message(
        chat_id,
        "📩 SMS doğrulama kodu gerekiyor. Telefonunuza gelen kodu buraya yazın:"
    )
    bot.register_next_step_handler(msg, sms_kodu_al)

    tamamlandi = sms_event[chat_id].wait(timeout=zaman_asimi)
    del sms_event[chat_id]

    if not tamamlandi:
        bot.send_message(chat_id, "SMS kodu zaman aşımına uğradı, tarama bir sonraki turda tekrar denenecek.")
        return None

    return sms_kod_deposu.pop(chat_id, None)


def otomatik_giris_yap(driver, chat_id):
    """
    Kayitli hesap bilgileriyle giris formunu doldurur. SMS adimi cikarsa
    kodu Telegram uzerinden ister. Basarili olursa True doner.
    """
    hesap = hesap_bilgileri.get(chat_id)
    if not hesap:
        return False

    print(f"[{zaman()}] [GİRİŞ] Otomatik giriş deneniyor... (chat_id: {chat_id})")

    eposta_alani = elemani_bul(driver, [
        (By.ID, "username"),
        (By.NAME, "username"),
        (By.ID, "j_username"),
        (By.NAME, "j_username"),
        (By.CSS_SELECTOR, "input[type='email']"),
        (By.CSS_SELECTOR, "input[name*='mail']"),
    ], bekle=10)

    sifre_alani = elemani_bul(driver, [
        (By.ID, "password"),
        (By.NAME, "password"),
        (By.ID, "j_password"),
        (By.NAME, "j_password"),
        (By.CSS_SELECTOR, "input[type='password']"),
    ], bekle=5)

    if not eposta_alani or not sifre_alani:
        print(f"[{zaman()}] [HATA] Giriş formu alanları bulunamadı. Sayfa yapısı değişmiş olabilir; "
              f"F12 ile inceleyip elemani_bul() listelerine yeni seçicileri ekleyin.")
        try:
            bot.send_message(chat_id, "Giriş formu otomatik doldurulamadı (alanlar bulunamadı). "
                                       "Lütfen açık olan tarayıcı penceresinden manuel giriş yapın.")
        except:
            pass
        return False

    eposta_alani.clear()
    eposta_alani.send_keys(hesap["eposta"])
    time.sleep(random.uniform(0.5, 1.2))

    sifre_alani.clear()
    sifre_alani.send_keys(hesap["sifre"])
    time.sleep(random.uniform(0.5, 1.2))

    # Buton type="submit" degil type="button", bu yuzden id ile aranıyor
    giris_butonu = elemani_bul(driver, [
        (By.ID, "userLoginSubmitButton"),
        (By.ID, "submitButton"),
        (By.CSS_SELECTOR, "button[type='submit']"),
        (By.XPATH, "//button[contains(translate(text(), 'GİRİŞ', 'giriş'), 'giriş')]"),
    ], bekle=5)

    if giris_butonu:
        giris_butonu.click()
    else:
        sifre_alani.submit()

    time.sleep(random.uniform(3.0, 5.0))

    sms_alani = elemani_bul(driver, [
        (By.ID, "smsCode"),
        (By.NAME, "smsCode"),
        (By.ID, "code"),
        (By.NAME, "code"),
        (By.CSS_SELECTOR, "input[name*='sms']"),
        (By.CSS_SELECTOR, "input[name*='otp']"),
    ], bekle=8)

    if sms_alani:
        kod = sms_kodu_iste(chat_id)
        if not kod:
            return False

        sms_alani.clear()
        sms_alani.send_keys(kod)
        time.sleep(0.5)

        onayla_butonu = elemani_bul(driver, [
            (By.ID, "submitButton"),
            (By.CSS_SELECTOR, "button[type='submit']"),
            (By.XPATH, "//button[contains(text(),'Onayla')]"),
        ], bekle=5)

        if onayla_butonu:
            onayla_butonu.click()
        else:
            sms_alani.submit()

        time.sleep(random.uniform(3.0, 5.0))

    basarili = dogrulama_gecildi_mi(driver)

    if basarili:
        print(f"[{zaman()}] [GİRİŞ] Otomatik giriş başarılı. (chat_id: {chat_id})")
    else:
        print(f"[{zaman()}] [HATA] Otomatik giriş başarısız görünüyor. (chat_id: {chat_id})")
        try:
            bot.send_message(chat_id, "Giriş denemesi başarısız görünüyor. Lütfen açık tarayıcı penceresinden "
                                       "kontrol edin, gerekirse manuel tamamlayın.")
        except:
            pass

    return basarili


DOGRULAMA_MAX_BEKLEME = 240  # saniye; asilirsa gorev atlanip sonraki turda denenir
DOGRULAMA_KONTROL_ARALIGI = 5


def dogrulama_gecildi_mi(driver):
    """URL hala giris/dogrulama sayfasindaysa False doner."""
    guncel_url = driver.current_url.lower()
    return "secure.sahibinden.com" not in guncel_url and "giris" not in guncel_url and "login" not in guncel_url


def dogrulamayi_bekle(driver, chat_id, azami_saniye=DOGRULAMA_MAX_BEKLEME):
    """
    Dogrulama ekraninin gecilmesini sinirli sure bekler. Sure dolarsa False
    doner ve tarama diger gorevlerle devam eder; boylece tek bir dogrulama
    ekrani tum botu kilitlemez.
    """
    baslangic = time.time()
    hatirlatma_gonderildi = False

    while time.time() - baslangic < azami_saniye:
        if dogrulama_gecildi_mi(driver):
            return True

        # Surenin yarisinda tek bir hatirlatma gonder
        if not hatirlatma_gonderildi and time.time() - baslangic > azami_saniye / 2:
            hatirlatma_gonderildi = True
            try:
                bot.send_message(chat_id, "Doğrulama hâlâ bekliyor, müsait olduğunuzda açık tarayıcı "
                                           "penceresinden tamamlayabilirsiniz.")
            except:
                pass

        time.sleep(DOGRULAMA_KONTROL_ARALIGI)

    return False


# ---------------------------------------------------------------
# ILAN AYRISTIRMA
# ---------------------------------------------------------------
def ilan_ayrintilarini_cikar(ilan):
    """
    Bir ilan satirindan baslik, link, fiyat ve satici tipini cikarir.
    Baslik veya link okunamazsa None doner.
    """
    baslik_etiketi = ilan.find("a", class_="classifiedTitle")
    if not (baslik_etiketi and baslik_etiketi.has_attr("href")):
        return None

    ilan_baslik = baslik_etiketi.text.strip()
    ham_link = baslik_etiketi["href"].strip()

    if "javascript" in ham_link.lower() or ham_link == "#" or ham_link == "":
        return None
    if "/ilan/" not in ham_link.lower():
        return None

    if ham_link.startswith("http"):
        ilan_link = ham_link
    elif ham_link.startswith("/"):
        ilan_link = "https://www.sahibinden.com" + ham_link
    else:
        ilan_link = "https://www.sahibinden.com/" + ham_link

    fiyat_etiketi = ilan.find("td", class_="searchResultsPriceValue")
    fiyat = 0
    if fiyat_etiketi:
        fiyat_metni = fiyat_etiketi.text.replace(".", "").replace("TL", "").replace(" ", "").replace("\n", "").strip()
        try:
            fiyat = int(fiyat_metni)
        except:
            fiyat = 0

    # Magaza ilanlarinda basligin yaninda magaza sayfasina giden bir link olur
    satici = "👤 Sahibinden"
    if ilan.find("a", class_="store-icon"):
        satici = "🏢 Emlakçı / Galeri"

    return ilan_baslik, ilan_link, fiyat, satici


# ---------------------------------------------------------------
# ANA TARAMA DONGUSU
# ---------------------------------------------------------------
def sahibinden_tarayici_dongusu():
    global ust_uste_dogrulama_sayisi

    print(f"\n[{zaman()}] [SİSTEM] Tarama motoru başlatılıyor. Tarayıcı ayağa kaldırılıyor...")
    driver = tarayici_olustur()
    print(f"[{zaman()}] [SİSTEM] Tarayıcı hazır. Görevler bekleniyor...\n")

    tarama_sayaci = 0

    while True:
        # Gece taramaya ara ver
        su_anki_saat = datetime.datetime.now().hour
        if 2 <= su_anki_saat <= 7:
            print(f"[{zaman()}] [UYKU] Saat {su_anki_saat}. Gece molası veriliyor.")
            time.sleep(3600)
            continue

        kullanici_listesi = list(aktif_kullanicilar.items())

        if not kullanici_listesi:
            print(f"[{zaman()}] [BEKLEMEDE] Kayıtlı görev yok, 30 saniye bekleniyor...")
            time.sleep(30)
            continue

        tarama_sayaci += 1
        print(f"\n--------------------------------------------------")
        print(f"[{zaman()}] [BİLGİ] TARAMA DÖNGÜSÜ BAŞLADI (Tur: {tarama_sayaci})")
        print(f"--------------------------------------------------")
        bu_turda_dogrulama_var = False

        # Sayfada bekleyen bir "Devam Et" ara ekrani varsa gec
        try:
            devam_butonlari = driver.find_elements(By.XPATH, "//*[contains(translate(text(), 'DEVAM ET', 'devam et'), 'devam')]")

            for buton in devam_butonlari:
                if buton.is_displayed() and buton.is_enabled():
                    print(f"[{zaman()}] [SİSTEM] 'Devam Et' butonu tespit edildi. Otomatik geçiliyor...")
                    # Butonun ustunde gorunmez katman olabildigi icin JS ile tiklatiyoruz
                    driver.execute_script("arguments[0].click();", buton)
                    time.sleep(random.uniform(4.0, 6.0))
                    break
        except Exception:
            pass

        for chat_id, gorevler in kullanici_listesi:
            for isim, data in gorevler.items():
                hedef_url = data.get("url")

                if not hedef_url:
                    continue

                if not data.get("aktif", True):
                    print(f"[{zaman()}] [DURAKLATILDI] '{isim}' görevi duraklatılmış, atlanıyor.")
                    continue

                # Uzun sureli oturumlarda tarayici sisiyor, arada sifirla
                if tarama_sayaci % 35 == 0:
                    try:
                        driver.quit()
                    except:
                        pass
                    os.system("taskkill /F /IM chrome.exe /T >nul 2>&1")
                    print(f"[{zaman()}] [SİSTEM] Rutin tarayıcı temizliği yapılıyor...")
                    time.sleep(10)
                    driver = tarayici_olustur()

                try:
                    print(f"[{zaman()}] [İŞLEM] Hedefe gidiliyor: '{isim}'")
                    driver.get("https://www.google.com")
                    time.sleep(2)

                    driver.get(hedef_url)
                    time.sleep(random.uniform(6.0, 9.5))

                    if not dogrulama_gecildi_mi(driver):
                        print(f"[{zaman()}] [UYARI] Doğrulama sayfasına düşüldü.")
                        bu_turda_dogrulama_var = True

                        giris_denendi = False
                        if chat_id in hesap_bilgileri:
                            giris_denendi = otomatik_giris_yap(driver, chat_id)

                        if not giris_denendi:
                            try:
                                bot.send_message(
                                    chat_id,
                                    "sahibinden engeline takıldık! Hesabınızı /hesap ile kaydetmediyseniz "
                                    "kaydedin, ya da açık olan tarayıcı penceresinden manuel giriş yapın."
                                )
                            except:
                                pass

                            gecildi = dogrulamayi_bekle(driver, chat_id)
                            if not gecildi:
                                print(f"[{zaman()}] [UYARI] Doğrulama {DOGRULAMA_MAX_BEKLEME} sn içinde "
                                      f"geçilmedi, '{isim}' bu tur atlanıyor, sonraki turda tekrar denenecek.")
                                continue

                            print(f"[{zaman()}] [BİLGİ] Doğrulama geçildi, tarama devam ediyor...")
                            try:
                                bot.send_message(chat_id, "Engel geçildi, taramaya kaldığım yerden devam ediyorum.")
                            except:
                                pass

                        driver.get(hedef_url)
                        time.sleep(random.uniform(5.0, 8.0))

                    soup = BeautifulSoup(driver.page_source, "html.parser")
                    ilanlar = soup.find_all("tr", class_="searchResultsItem")

                    yeni_ilan_bulundu_mu = False
                    fiyat_guncellendi_mi = False
                    # Mesajlar once toplanir, sonra sirali gonderilir
                    yeni_ilanlar = []
                    fiyat_degisiklikleri = []

                    for ilan in ilanlar:
                        ilan_id = ilan.get("data-id")

                        # 84 ile baslayan id'ler ilan degil, reklam/banner satiri
                        if ilan_id and str(ilan_id).startswith("84"):
                            continue

                        reklam_etiketi = ilan.find("td", class_="searchResultsPromoValue")
                        if reklam_etiketi and "Sponsorlu" in reklam_etiketi.text:
                            continue

                        # --- Daha once gorulmemis ilan ---
                        if ilan_id and ilan_id.isdigit() and ilan_id not in data["gorulen_ilanlar"]:
                            ayrinti = ilan_ayrintilarini_cikar(ilan)
                            if ayrinti is None:
                                continue
                            ilan_baslik, ilan_link, fiyat, satici = ayrinti

                            firsat_durumu = ""
                            if fiyat > 0:
                                fiyat_gecmisi = data.get("fiyatlar", [])
                                # Medyan, guncel fiyat listeye eklenmeden once hesaplanir
                                if len(fiyat_gecmisi) >= 5:
                                    medyan = statistics.median(fiyat_gecmisi)
                                    if fiyat < (medyan * 0.85):
                                        firsat_durumu = "\n🚨 DİKKAT: PİYASA ORTALAMASININ %15 ALTINDA!"

                                fiyat_gecmisi.append(fiyat)
                                if len(fiyat_gecmisi) > 200:
                                    fiyat_gecmisi.pop(0)
                                data["fiyatlar"] = fiyat_gecmisi
                                data.setdefault("ilan_fiyatlari", {})[ilan_id] = fiyat

                            if not data.get("ilk_tarama", True):
                                mesaj = f"<b>YENİ İLAN TESPİT EDİLDİ ({isim})</b>\n\n<b>Başlık:</b> {ilan_baslik}\n<b>Satıcı:</b> {satici}\n<b>Fiyat:</b> {fiyat:,} TL{firsat_durumu}\n<b>Bağlantı:</b> {ilan_link}"
                                yeni_ilanlar.append((int(ilan_id), mesaj))

                            data["gorulen_ilanlar"].add(ilan_id)
                            yeni_ilan_bulundu_mu = True

                        # --- Bilinen ilan: fiyati degismis mi? ---
                        elif ilan_id and ilan_id.isdigit() and ilan_id in data["gorulen_ilanlar"]:
                            ayrinti = ilan_ayrintilarini_cikar(ilan)
                            if ayrinti is None:
                                continue
                            ilan_baslik, ilan_link, fiyat, satici = ayrinti

                            if fiyat > 0:
                                onceki_fiyat = data.get("ilan_fiyatlari", {}).get(ilan_id)

                                if onceki_fiyat != fiyat:
                                    # onceki_fiyat None ise fiyat ilk kez kaydediliyor demektir,
                                    # kiyaslanacak eski deger olmadigi icin bildirim gonderilmez
                                    if onceki_fiyat is not None and not data.get("ilk_tarama", True):
                                        fark = fiyat - onceki_fiyat
                                        yuzde = abs(fark) / onceki_fiyat * 100

                                        firsat_durumu = ""
                                        gecmis_kontrol = data.get("fiyatlar", [])
                                        if len(gecmis_kontrol) >= 5:
                                            medyan = statistics.median(gecmis_kontrol)
                                            if fiyat < (medyan * 0.85):
                                                firsat_durumu = "\n🚨 DİKKAT: PİYASA ORTALAMASININ %15 ALTINDA!"

                                        if fark > 0:
                                            baslik_satiri = f"📈 <b>FİYAT ARTTI ({isim})</b>"
                                            yon_metni = f"+{fark:,} TL (%{yuzde:.1f})"
                                        else:
                                            baslik_satiri = f"📉 <b>FİYAT DÜŞTÜ ({isim})</b>"
                                            yon_metni = f"-{abs(fark):,} TL (%{yuzde:.1f})"

                                        mesaj = (f"{baslik_satiri}\n\n<b>Başlık:</b> {ilan_baslik}\n<b>Satıcı:</b> {satici}\n"
                                                 f"<b>Eski Fiyat:</b> {onceki_fiyat:,} TL\n<b>Yeni Fiyat:</b> {fiyat:,} TL{firsat_durumu}\n"
                                                 f"<b>Değişim:</b> {yon_metni}\n<b>Bağlantı:</b> {ilan_link}")
                                        fiyat_degisiklikleri.append((int(ilan_id), mesaj))

                                    # Sadece fiyat gercekten degistiginde listeye eklenir,
                                    # yoksa medyan ayni degerlerle dolup anlamsizlasir
                                    fiyat_gecmisi = data.get("fiyatlar", [])
                                    fiyat_gecmisi.append(fiyat)
                                    if len(fiyat_gecmisi) > 200:
                                        fiyat_gecmisi.pop(0)
                                    data["fiyatlar"] = fiyat_gecmisi

                                    data.setdefault("ilan_fiyatlari", {})[ilan_id] = fiyat
                                    fiyat_guncellendi_mi = True

                    # Sahibinden id'leri zamanla arttigi icin buyuk id = yeni ilan.
                    # Sayfa sirasina guvenmek yerine yeniden eskiye siralanir.
                    yeni_ilanlar.sort(key=lambda oge: oge[0], reverse=True)
                    for _, mesaj in yeni_ilanlar:
                        try:
                            bot.send_message(chat_id, mesaj, parse_mode="HTML")
                        except:
                            pass
                        time.sleep(1)

                    fiyat_degisiklikleri.sort(key=lambda oge: oge[0], reverse=True)
                    for _, mesaj in fiyat_degisiklikleri:
                        try:
                            bot.send_message(chat_id, mesaj, parse_mode="HTML")
                        except:
                            pass
                        time.sleep(1)

                    if yeni_ilan_bulundu_mu or fiyat_guncellendi_mi:
                        verileri_kaydet()

                    if data.get("ilk_tarama", True):
                        data["ilk_tarama"] = False
                        verileri_kaydet()

                    print(f"[{zaman()}] [BAŞARILI] '{isim}' görevi tarandı.")

                except Exception as e:
                    print(f"[{zaman()}] [HATA] '{isim}' görevi taranırken sorun oluştu: {e}")

        # Dogrulama ekraniyla karsilasildikca bekleme suresi uzar, sorunsuz
        # bir tur gecince normale doner
        if bu_turda_dogrulama_var:
            ust_uste_dogrulama_sayisi += 1
        else:
            ust_uste_dogrulama_sayisi = 0

        taban_bekleme = random.uniform(240, 420)
        ek_bekleme = min(ust_uste_dogrulama_sayisi * 120, 900)
        bekleme = taban_bekleme + ek_bekleme

        if ek_bekleme > 0:
            print(f"[{zaman()}] [BİLGİ] Son turlarda doğrulamayla karşılaşıldı "
                  f"({ust_uste_dogrulama_sayisi} kez üst üste), bekleme süresi {int(ek_bekleme)} sn uzatıldı.")

        print(f"[{zaman()}] [BEKLEME] Tur tamamlandı. Sonraki tura kadar {int(bekleme)} saniye bekleniyor...\n")
        time.sleep(bekleme)


def telegram_polling_dongusu():
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=20, skip_pending=True)
        except:
            time.sleep(10)


if __name__ == "__main__":
    verileri_yukle()
    hesap_verilerini_yukle()

    try:
        bot.remove_webhook()
        time.sleep(1)
    except:
        pass

    # Tarama arka planda calisir, ana thread Telegram mesajlarini dinler
    t = threading.Thread(target=sahibinden_tarayici_dongusu, daemon=True)
    t.start()
    print("Telegram botu aktif, mesajlar dinleniyor...\n")
    telegram_polling_dongusu()
