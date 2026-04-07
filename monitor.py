import os
import time
import logging
import requests
from datetime import datetime

# ─── إعدادات تيليغرام ───────────────────────────────────────────
TG_TOKEN  = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

# ─── فترة الفحص (بالدقائق) ──────────────────────────────────────
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "10"))

# ─── السفارات المراقَبة ──────────────────────────────────────────
EMBASSIES = [
    {
        "id": "france",
        "name": "🇫🇷 فرنسا (TLScontact)",
        "url": "https://ma.tlscontact.com/fr/CAS/index.php",
        "keywords": ["disponible", "créneau", "appointment", "rdv"],
    },
    {
        "id": "spain",
        "name": "🇪🇸 إسبانيا (BLS)",
        "url": "https://blsspainmorocco.com/casablanca/arabic/",
        "keywords": ["appointment", "موعد", "disponible", "slot"],
    },
    {
        "id": "germany",
        "name": "🇩🇪 ألمانيا (TLScontact)",
        "url": "https://ma.tlscontact.com/de/RBA/index.php",
        "keywords": ["disponible", "créneau", "termin", "appointment"],
    },
    {
        "id": "italy",
        "name": "🇮🇹 إيطاليا (VFS Global)",
        "url": "https://visa.vfsglobal.com/mar/ar/ita/",
        "keywords": ["appointment", "موعد", "available", "slot"],
    },
]

# ─── إعداد السجل ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("visa-monitor")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# تتبع حالة كل سفارة لتجنب إرسال تنبيهات متكررة
last_state: dict[str, bool] = {e["id"]: False for e in EMBASSIES}


def send_telegram(message: str) -> bool:
    if not TG_TOKEN or not TG_CHAT_ID:
        log.warning("تيليغرام غير مُعدّ — تخطي الإشعار")
        return False
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            json={"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        if r.status_code == 200:
            log.info("تم إرسال تنبيه تيليغرام بنجاح")
            return True
        log.error(f"خطأ تيليغرام: {r.text}")
    except Exception as e:
        log.error(f"فشل إرسال تيليغرام: {e}")
    return False


def check_embassy(embassy: dict) -> bool:
    try:
        r = requests.get(embassy["url"], headers=HEADERS, timeout=15)
        content = r.text.lower()
        for kw in embassy["keywords"]:
            if kw.lower() in content:
                return True
        return False
    except Exception as e:
        log.error(f"خطأ في فحص {embassy['name']}: {e}")
        return False


def run_check():
    log.info("=" * 50)
    log.info(f"بدء دورة الفحص — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    for emb in EMBASSIES:
        available = check_embassy(emb)
        prev = last_state[emb["id"]]

        if available and not prev:
            # موعد جديد ظهر → أرسل تنبيهاً
            msg = (
                f"🚨 <b>موعد فيزا متاح!</b>\n\n"
                f"السفارة: {emb['name']}\n"
                f"الرابط: {emb['url']}\n\n"
                f"⚡ تصرف بسرعة قبل أن يُحجز!"
            )
            send_telegram(msg)
            log.info(f"✅ موعد متاح — {emb['name']}")
        elif not available and prev:
            # كان متاحاً والآن اختفى
            log.info(f"❌ اختفى الموعد — {emb['name']}")
            send_telegram(f"ℹ️ اختفى الموعد في {emb['name']} — استمرار المراقبة.")
        else:
            status = "متاح" if available else "غير متاح"
            log.info(f"{'✅' if available else '⏳'} {emb['name']} — {status}")

        last_state[emb["id"]] = available

    log.info(f"انتظار {CHECK_INTERVAL} دقيقة للفحص القادم...")


def main():
    log.info("🚀 بدء مراقبة مواعيد الفيزا")
    log.info(f"السفارات: {len(EMBASSIES)} | الفترة: {CHECK_INTERVAL} دقيقة")

    if TG_TOKEN and TG_CHAT_ID:
        send_telegram("✅ <b>بدأت مراقبة مواعيد الفيزا</b>\n\nسيصلك إشعار فور ظهور موعد.")
        log.info("تيليغرام مفعّل")
    else:
        log.warning("تيليغرام غير مُعدّ — سيعمل السكريبت بدون إشعارات")

    while True:
        run_check()
        time.sleep(CHECK_INTERVAL * 60)


if __name__ == "__main__":
    main()
