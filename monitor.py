import os
import time
import logging
import requests
from datetime import datetime

# ─── إعدادات تيليغرام
TG_TOKEN   = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "15"))

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
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8,ar;q=0.7",
}

EMBASSIES = [
    {
        "id": "france_tls",
        "name": "U0001f1ebU0001f1f7 فرنسا — TLScontact الدار البيضاء",
        "url": "https://ma.tlscontact.com/fr/CAS/index.php",
        "api_url": "https://ma.tlscontact.com/fr/CAS/rdv/api/slots?country=MA&city=CAS",
        "mode": "api",
        "fallback_url": "https://ma.tlscontact.com/fr/CAS/index.php",
        "positive_keywords": ["disponible", "slot", "créneau", "rdv"],
        "negative_keywords": ["aucun", "no slot", "complet", "indisponible"],
    },
    {
        "id": "spain_bls",
        "name": "U0001f1eaU0001f1f8 إسبانيا — BLS الدار البيضاء",
        "url": "https://blsspainmorocco.com/casablanca/arabic/",
        "api_url": None,
        "mode": "scrape",
        "fallback_url": "https://blsspainmorocco.com/casablanca/arabic/",
        "positive_keywords": ["appointment", "موعد", "disponible", "slot", "available"],
        "negative_keywords": ["لا يوجد", "no appointment", "not available"],
    },
    {
        "id": "germany_tls",
        "name": "U0001f1e9U0001f1ea ألمانيا — TLScontact الرباط",
        "url": "https://ma.tlscontact.com/de/RBA/index.php",
        "api_url": "https://ma.tlscontact.com/de/RBA/rdv/api/slots?country=MA&city=RBA",
        "mode": "api",
        "fallback_url": "https://ma.tlscontact.com/de/RBA/index.php",
        "positive_keywords": ["disponible", "slot", "créneau", "termin", "appointment"],
        "negative_keywords": ["aucun", "no slot", "complet", "indisponible"],
    },
    {
        "id": "italy_vfs",
        "name": "U0001f1eeU0001f1f9 إيطاليا — VFS Global الدار البيضاء",
        "url": "https://visa.vfsglobal.com/mar/ar/ita/",
        "api_url": "https://visa.vfsglobal.com/api/appointment/slots?country=mar&mission=ita",
        "mode": "api",
        "fallback_url": "https://visa.vfsglobal.com/mar/ar/ita/",
        "positive_keywords": ["appointment", "موعد", "available", "slot"],
        "negative_keywords": ["no appointment", "not available", "لا يوجد"],
    },
]

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
        log.error(f"خطأ تيليغرام {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log.error(f"فشل إرسال تيليغرام: {e}")
    return False


def _parse_content(content: str, positive_kw: list, negative_kw: list) -> bool:
    lower = content.lower()
    for neg in negative_kw:
        if neg.lower() in lower:
            return False
    for pos in positive_kw:
        if pos.lower() in lower:
            return True
    return False


def check_embassy_api(embassy: dict):
    api_url = embassy.get("api_url")
    if not api_url:
        return None
    try:
        r = requests.get(api_url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            result = _parse_content(r.text, embassy["positive_keywords"], embassy["negative_keywords"])
            log.debug(f"[API] {embassy[\'name\']} -> {r.text[:120]}")
            return result
        elif r.status_code in (403, 401, 429):
            log.warning(f"[API] {embassy[\'name\']} محجوب ({r.status_code}) -> fallback")
            return None
        else:
            log.warning(f"[API] {embassy[\'name\']} status {r.status_code} -> fallback")
            return None
    except Exception as e:
        log.warning(f"[API] {embassy[\'name\']} خطأ: {e} -> fallback")
        return None


def check_embassy_scrape(embassy: dict, use_fallback: bool = False) -> bool:
    url = embassy["fallback_url"] if use_fallback else embassy["url"]
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            log.warning(f"[Scrape] {embassy[\'name\']} status {r.status_code} — تخطي")
            return False
        return _parse_content(r.text, embassy["positive_keywords"], embassy["negative_keywords"])
    except Exception as e:
        log.error(f"[Scrape] خطأ في فحص {embassy[\'name\']}: {e}")
        return False


def check_embassy(embassy: dict) -> bool:
    if embassy["mode"] == "api":
        result = check_embassy_api(embassy)
        if result is not None:
            return result
        log.info(f"[Fallback] {embassy[\'name\']} -> scraping")
        return check_embassy_scrape(embassy, use_fallback=True)
    else:
        return check_embassy_scrape(embassy)


def run_check():
    log.info("=" * 55)
    log.info(f"بدء دورة الفحص — {datetime.now().strftime(\'%Y-%m-%d %H:%M:%S\'  )}")
    for emb in EMBASSIES:
        available = check_embassy(emb)
        prev = last_state[emb["id"]]

        if available and not prev:
            msg = (
                f"U0001f6a8 <b>موعد فيزا متاح!</b>\n\n"
                f"السفارة: {emb[\'name\']}\n"
                f"الرابط: {emb[\'url\']}\n\n"
                f"⚡ تصرف بسرعة قبل أن يُحجز!"
            )
            send_telegram(msg)
            log.info(f"✅ موعد متاح — {emb[\'name\']}")
        elif not available and prev:
            log.info(f"❌ اختفى الموعد — {emb[\'name\']}")
            send_telegram(f"ℹ️ اختفى الموعد في {emb[\'name\']} — استمرار المراقبة.")
        else:
            status = "متاح" if available else "غير متاح"
            log.info(f"{✅ if available else ⏳} {emb[\'name\']} — {status}")

        last_state[emb["id"]] = available

    log.info(f"انتظار {CHECK_INTERVAL} دقيقة للفحص القادم...")


def main():
    log.info("U0001f680 بدء مراقبة مواعيد الفيزا — v2")
    log.info(f"السفارات: {len(EMBASSIES)} | الفترة: {CHECK_INTERVAL} دقيقة")
    if TG_TOKEN and TG_CHAT_ID:
        send_telegram(
            "✅ <b>بدأت مراقبة مواعيد الفيزا (v2)</b>\n\n"
            "يستخدم API مباشر + fallback ذكي.\n"
            "سيصلك إشعار فور ظهور موعد."
        )
        log.info("تيليغرام مفعّل")
    else:
        log.warning("تيليغرام غير مُعدّ — سيعمل بدون إشعارات")
    while True:
        run_check()
        time.sleep(CHECK_INTERVAL * 60)


if __name__ == "__main__":
    main()
