"""Константы интеграции ТЕКК (lk.tekk.pro)."""

DOMAIN = "tekk_lk"

BASE_URL = "https://lk.tekk.pro"

CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL_MIN = 360
MIN_SCAN_INTERVAL_MIN = 15

REQUEST_TIMEOUT = 30
LOGIN_RETRY_PAUSE = 300  # секунд паузы после неудачного входа
REPORT_DAYS = 92  # за сколько дней читать списания и платежи
