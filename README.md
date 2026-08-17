# Sahibinden İlan Takip Botu

Kaydettiğiniz sahibinden.com arama linklerini belirli aralıklarla tarar; yeni çıkan
ilanları ve daha önce görülmüş ilanların fiyat değişimlerini Telegram üzerinden bildirir.

Kişisel kullanım için yazılmış bir araçtır.
SAHİBİNDEN.COM İLE HİÇBİR BAĞLANTISI YOKTUR.İZİNSİZ PAZARLANMASI KESİNLİKLE YASAKTIR.

## Özellikler

- Birden fazla arama linkini aynı anda takip etme
- Yeni ilan bildirimi (başlık, fiyat, satıcı tipi, bağlantı)
- Fiyat değişikliği bildirimi — düşüş 📉 / artış 📈, fark ve yüzde ile birlikte
- Satıcı ayrımı: sahibinden mi, emlakçı/galeri mi
- Piyasa medyanının %15 altındaki ilanlar için fırsat uyarısı
- Aramayı silmeden geçici duraklatma (`/duraklat`)
- Arama linkini silmeden güncelleme (`/duzenle`)
- Yönetici paneli: tüm kullanıcıların aramalarını görme ve iptal etme (`/admin`)
- Gece saatlerinde (02:00–07:00) otomatik mola

## Kurulum

Gereksinimler: Python 3.9+, Google Chrome, Windows.

```bash
pip install -r requirements.txt
```

Ayarları hazırlayın:

```bash
copy .env.example .env
```

`.env` dosyasını doldurun:

| Değişken | Açıklama |
| --- | --- |
| `TELEGRAM_TOKEN` | [@BotFather](https://t.me/BotFather)'dan aldığınız bot token'ı |
| `IZINLI_KULLANICILAR` | Botu kullanabilecek chat id'ler, virgülle ayrılmış |
| `ADMIN_KULLANICILAR` | Tüm aramaları görüp iptal edebilecek chat id'ler |

Chat id'nizi öğrenmek için bota `/start` yazın; izinli listede değilseniz konsola
`İzinsiz giriş denemesi engellendi! (ID: ...)` satırı düşer, oradaki id'yi kullanın.

Çalıştırın:

```bash
python sahibot.py
```

İlk çalıştırmada bir Chrome penceresi açılır. Sahibinden'e bu pencereden giriş
yapın; oturum `sahibinden_profil/` klasöründe saklanır ve sonraki çalıştırmalarda
korunur.

## Kullanım

| Komut | Açıklama |
| --- | --- |
| Link yapıştırmak | Yeni arama ekler, ardından bir isim sorar |
| `/liste` | Kayıtlı aramaları durumlarıyla listeler |
| `/sil` | Bir aramayı siler |
| `/duraklat` | Aramayı geçici durdurur veya devam ettirir |
| `/duzenle` | Aramanın linkini değiştirir |
| `/hesap` | Sahibinden hesap bilgilerini kaydeder (otomatik giriş için) |
| `/hesapsil` | Kayıtlı hesap bilgilerini siler |
| `/admin` | Tüm kullanıcıların aramalarını yönetir (sadece yönetici) |

Yeni bir arama eklendiğinde ilk tarama sessiz geçer: mevcut ilanlar bildirim
gönderilmeden kaydedilir. Bildirimler ikinci turdan itibaren başlar.

## Veri dosyaları

Bu dosyalar çalışma sırasında oluşur ve `.gitignore` içindedir:

- `kullanici_verileri.json` — kayıtlı aramalar, görülen ilanlar, fiyat geçmişi
- `hesap_bilgileri.json` — sahibinden hesap bilgileri, **düz metin**
- `sahibinden_profil/` — Chrome oturum profili, giriş çerezlerini içerir

`hesap_bilgileri.json` şifreyi şifrelenmemiş biçimde saklar. Dosyayı paylaşmayın,
yedeklere dahil etmeyin ve yalnızca kendi hesabınızla kullanın.

## Bilinen sınırlamalar

Sahibinden otomatik erişimi tespit eden koruma sistemleri kullanır. Bot bir
doğrulama ekranına düştüğünde:

- Kayıtlı hesap varsa otomatik giriş denenir, SMS istenirse kod Telegram'dan sorulur
- Doğrulama geçilemezse en fazla 4 dakika beklenir, sonra o görev atlanır ve
  sonraki turda tekrar denenir
- Üst üste doğrulamayla karşılaşıldıkça turlar arası bekleme kademeli uzar
  (en fazla +15 dakika), böylece siteye binen yük azalır

"Basılı tutun" gibi insan doğrulaması ekranları **elle** tamamlanmalıdır; bot bu
ekranları geçmeye çalışmaz, açık tarayıcı penceresinden tamamlamanızı bekler.
Tarama aralığı bilinçli olarak geniş tutulmuştur (4–7 dakika).

## Sorumluluk

Bu araç kişisel kullanım içindir. Kullanımından doğacak sorumluluk kullanıcıya
aittir; sahibinden.com kullanım şartlarına uygun hareket ettiğinizden emin olun.
