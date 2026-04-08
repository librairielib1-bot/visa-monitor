from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import requests


TG_TOKEN = os.environ.get("TG_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "25"))

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


@dataclass(frozen=True)
class Embassy:
    id: str
    name: str
    url: str
    method: str
    api_url: str | None = None
    fallback_url: str | None = None
    positive_keywords: tuple[str, ...] = ()
    negative_keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class CheckResult:
    embassy: Embassy
    status: str
    detail: str


EMBASSIES: tuple[Embassy, ...] = (
    Embassy(
        id="france_tls",
        name="France - TLScontact Casablanca",
        url="https://ma.tlscontact.com/fr/CAS/index.php",
        method="json-api",
        api_url="https://ma.tlscontact.com/fr/CAS/rdv/api/slots?country=MA&city=CAS",
        fallback_url="https://ma.tlscontact.com/fr/CAS/index.php",
        positive_keywords=("slot available", "available", "creaneau", "creneau", "disponible"),
        negative_keywords=("aucun", "no slot", "complet", "indisponible"),
    ),
    Embassy(
        id="spain_bls",
        name="Spain - BLS Casablanca",
        url="https://blsspainmorocco.com/casablanca/",
        method="html-scrape",
        fallback_url="https://blsspainmorocco.com/casablanca/",
        positive_keywords=("slot available", "slots available", "appointment available", "choose date", "select date"),
        negative_keywords=(
            "no appointment slots are currently available",
            "no appointments available",
            "all appointments are booked",
            "fully booked",
            "aucun rendez-vous",
            "pas de rendez-vous disponible",
            "complet",
        ),
    ),
    Embassy(
        id="germany_tls",
        name="Germany - TLScontact Rabat",
        url="https://ma.tlscontact.com/de/RBA/index.php",
        method="json-api",
        api_url="https://ma.tlscontact.com/de/RBA/rdv/api/slots?country=MA&city=RBA",
        fallback_url="https://ma.tlscontact.com/de/RBA/index.php",
        positive_keywords=("slot available", "available", "termin", "creneau", "disponible"),
        negative_keywords=("aucun", "no slot", "complet", "indisponible"),
    ),
    Embassy(
        id="italy_vfs",
        name="Italy - VFS Global Casablanca",
        url="https://visa.vfsglobal.com/mar/ar/ita/",
        method="json-api",
        api_url="https://visa.vfsglobal.com/api/appointment/slots?country=mar&mission=ita",
        fallback_url="https://visa.vfsglobal.com/mar/ar/ita/",
        positive_keywords=("slot available", "slots available", "available appointments", "select date"),
        negative_keywords=("no appointment", "not available", "fully booked", "complet", "no slots"),
    ),
)


def send_telegram(message: str) -> bool:
    if not TG_TOKEN or not TG_CHAT_ID:
        log.warning("Telegram is not configured. Skipping notification.")
        return False

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": message},
            timeout=10,
        )
        response.raise_for_status()
        return True
    except Exception as exc:
        log.error("Telegram send failed: %s", exc)
        return False


def normalize_text(value: str) -> str:
    return " ".join((value or "").lower().split())


def parse_text_result(text: str, embassy: Embassy) -> CheckResult:
    normalized = normalize_text(text)

    for keyword in embassy.negative_keywords:
        if keyword in normalized:
            return CheckResult(embassy, "unavailable", f"keyword:{keyword}")

    for keyword in embassy.positive_keywords:
        if keyword in normalized:
            return CheckResult(embassy, "available", f"keyword:{keyword}")

    return CheckResult(embassy, "unknown", "no_signal")


def extract_json_slots(payload: Any) -> bool | None:
    if isinstance(payload, dict):
        for key in ("slots", "availableSlots", "available_dates", "availableDates", "data"):
            if key in payload:
                value = payload[key]
                if isinstance(value, list):
                    return len(value) > 0
                if isinstance(value, dict):
                    return len(value) > 0
        for value in payload.values():
            nested = extract_json_slots(value)
            if nested is not None:
                return nested
    if isinstance(payload, list):
        return len(payload) > 0
    return None


def fetch_json(session: requests.Session, url: str) -> Any:
    response = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def fetch_text(session: requests.Session, url: str) -> str:
    response = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.text


def check_json_api(session: requests.Session, embassy: Embassy) -> CheckResult:
    if not embassy.api_url:
        return CheckResult(embassy, "error", "missing_api_url")

    try:
        payload = fetch_json(session, embassy.api_url)
        slots = extract_json_slots(payload)
        if slots is True:
            return CheckResult(embassy, "available", "api_slots_present")
        if slots is False:
            return CheckResult(embassy, "unavailable", "api_slots_empty")
        return parse_text_result(json.dumps(payload, ensure_ascii=False), embassy)
    except Exception as exc:
        log.warning("%s API failed: %s", embassy.name, exc)
        if embassy.fallback_url:
            return check_html_scrape(session, embassy, embassy.fallback_url, detail_prefix="fallback")
        return CheckResult(embassy, "error", f"api_error:{type(exc).__name__}")


def check_html_scrape(
    session: requests.Session,
    embassy: Embassy,
    url: str | None = None,
    detail_prefix: str = "scrape",
) -> CheckResult:
    target_url = url or embassy.url
    try:
        text = fetch_text(session, target_url)
        result = parse_text_result(text, embassy)
        if embassy.id == "spain_bls" and result.status == "unknown":
            return CheckResult(embassy, "unknown", "public_page_has_no_live_slot_signal")
        return CheckResult(embassy, result.status, f"{detail_prefix}:{result.detail}")
    except Exception as exc:
        return CheckResult(embassy, "error", f"{detail_prefix}_error:{type(exc).__name__}")


def check_embassy(session: requests.Session, embassy: Embassy) -> CheckResult:
    if embassy.method == "json-api":
        return check_json_api(session, embassy)
    return check_html_scrape(session, embassy)


def format_result_line(result: CheckResult) -> str:
    return f"- {result.embassy.name}: {result.status} ({result.detail})"


def build_telegram_message(results: list[CheckResult]) -> str:
    available = [r for r in results if r.status == "available"]
    errors = [r for r in results if r.status == "error"]
    unknown = [r for r in results if r.status == "unknown"]
    unavailable = [r for r in results if r.status == "unavailable"]

    lines = ["Visa monitor summary", ""]
    lines.append(f"available={len(available)} unavailable={len(unavailable)} unknown={len(unknown)} error={len(errors)}")
    lines.append("")
    lines.extend(format_result_line(result) for result in results)

    if available:
        lines.append("")
        lines.append("Available links:")
        for result in available:
            lines.append(result.embassy.url)

    return "\n".join(lines)


def main() -> int:
    session = requests.Session()
    results: list[CheckResult] = []

    for embassy in EMBASSIES:
        result = check_embassy(session, embassy)
        results.append(result)
        log.info("%s -> %s | %s", embassy.name, result.status, result.detail)

    message = build_telegram_message(results)
    if message:
        send_telegram(message)

    available_count = sum(1 for result in results if result.status == "available")
    error_count = sum(1 for result in results if result.status == "error")

    log.info("Run finished. available=%s error=%s total=%s", available_count, error_count, len(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
