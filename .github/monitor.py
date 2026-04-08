import os
import time
import logging
import requests
from datetime import datetime

TG_TOKEN   = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "1"))

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
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

# ─── تعريف السفارات مع API مباشر ────────────────────────────────
EMBASSIES = [
    {
        "id": "france",
        "name": "🇫🇷 فرنسا (TLScontact)",
        "link": "https://ma.tlscontact.com/fr/CAS/index.php",
        "method": "tls",
        "tls_country": "fr",
        "tls_city": "CAS",
    },
    {
        "id": "spain",
        "name": "🇪🇸 إسبانيا (BLS)",
        "link": "https://blsspainmorocco.com/casablanca/arabic/",
        "method": "bls",
    },
    {
        "id": "germany",
        "name": "🇩🇪 ألمانيا (TLScontact)",
        "link": "https://ma.tlscontact.com/de/RBA/index.php",
        "method": "tls",
        "tls_country": "de",
        "tls_city": "RBA",
    },
    {
        "id": "italy",
        "name": "🇮🇹 إيطاليا (VFS Global)",
        "link": "https://visa.vfsglobal.com/mar/ar/ita/",
        "method": "vfs",
        "vfs_country": "mar",
        "vfs_mission": "ita",
    },
]

last_state: dict[str, bool] = {e["id"]: False for e in EMBASSIES}


def send_telegram(message: str) -> bool:
    if not TG_TOKEN or not TG_CHAT_ID:
        log.warning("تيليغرام غير مُعدّ")
        return False
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            json={"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        if r.status_code == 200:
            log.info("تم إرسال تنبيه تيليغرام")
            return True
        log.error(f"خطأ تيليغرام: {r.text}")
    except Exception as e:
        log.error(f"فشل تيليغرام: {e}")
    return False


def check_tls(emb: dict) -> bool:
    """TLScontact — يستخدم API الداخلي لجلب المواعيد المتاحة"""
    try:
        url = (
            f"https://ma.tlscontact.com/api/scheduling/v1/"
            f"locations/{emb['tls_country']}/{emb['tls_city']}/slots"
        )
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        slots = data.get("slots") or data.get("availableSlots") or []
        return len(slots) > 0
    except requests.exceptions.HTTPError as e:
        log.warning(f"TLS HTTP error {e.response.status_code} — {emb['name']}")
        return False
    except Exception as e:
        log.error(f"خطأ TLS {emb['name']}: {e}")
        return False


def check_bls(emb: dict) -> bool:
    """BLS إسبانيا — يفحص API المواعيد"""
    try:
        url = "https://blsspainmorocco.com/api/appointment/slots"
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        slots = data.get("slots") or data.get("data") or []
        return len(slots) > 0
    except requests.exceptions.HTTPError as e:
        log.warning(f"BLS HTTP error {e.response.status_code}")
        # fallback: scraping بسيط
        return check_fallback(emb)
    except Exception as e:
        log.error(f"خطأ BLS: {e}")
        return check_fallback(emb)


def check_vfs(emb: dict) -> bool:
    """VFS Global — يستخدم API الداخلي"""
    try:
        url = (
            f"https://lift-api.vfsglobal.com/appointment/slots"
            f"?countryCode={emb['vfs_country']}&missionCode={emb['vfs_mission']}"
        )
        headers = {**HEADERS, "Referer": emb["link"]}
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        slots = data.get("slots") or data.get("availableDates") or []
        return len(slots) > 0
    except requests.exceptions.HTTPError as e:
        log.warning(f"VFS HTTP error {e.response.status_code}")
        return check_fallback(emb)
    except Exception as e:
        log.error(f"خطأ VFS: {e}")
        return check_fallback(emb)


def check_fallback(emb: dict) -> bool:
    """Fallback: scraping مع فحص HTTP status"""
    try:
        r = requests.get(emb["link"], headers=HEADERS, timeout=15)
        r.raise_for_status()
        content = r.text.lower()
        negative = ["no appointment", "aucun créneau", "pas de rendez", "no slot",
                    "لا يوجد موعد", "complet", "fully booked"]
        positive = ["select date", "choisir", "disponible", "available",
                    "book now", "réserver", "موعد متاح"]
        if any(k in content for k in negative):
            return False
        return any(k in content for k in positive)
    except Exception as e:
        log.error(f"خطأ fallback {emb.get('name','')}: {e}")
        return False


def check_embassy(emb: dict) -> bool:
    method = emb.get("method", "fallback")
    if method == "tls":
        return check_tls(emb)
    elif method == "bls":
        return check_bls(emb)
    elif method == "vfs":
        return check_vfs(emb)
    return check_fallback(emb)


def run_check():
    log.info(f"{'='*50}")
    log.info(f"دورة فحص — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    for emb in EMBASSIES:
        available = check_embassy(emb)
        prev = last_state[emb["id"]]
        if available and not prev:
            msg = (
                f"🚨 <b>موعد فيزا متاح!</b>\n\n"
                f"السفارة: {emb['name']}\n"
                f"الرابط: {emb['link']}\n\n"
                f"⚡ تصرف بسرعة!"
            )
            send_telegram(msg)
            log.info(f"✅ موعد متاح — {emb['name']}")
        elif not available and prev:
            send_telegram(f"ℹ️ اختفى الموعد في {emb['name']}")
            log.info(f"❌ اختفى الموعد — {emb['name']}")
        else:
            log.info(f"{'✅' if available else '⏳'} {emb['name']} — {'متاح' if available else 'غير متاح'}")
        last_state[emb["id"]] = available


def main():
    log.info("🚀 بدء مراقبة مواعيد الفيزا v2")
    if TG_TOKEN and TG_CHAT_ID:
        send_telegram("✅ <b>مراقبة الفيزا v2 تعمل</b>\n\nفحص API مباشر لكل سفارة.")
    run_check()


if __name__ == "__main__":
    main()
