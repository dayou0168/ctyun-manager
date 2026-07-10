import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def env_text(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value


class Settings:
    admin_user = env_text("CTYUN_MANAGER_ADMIN_USER", "admin")
    admin_password = env_text("CTYUN_MANAGER_ADMIN_PASSWORD", "change-me-now")
    master_key = env_text("CTYUN_MANAGER_MASTER_KEY", "")
    db_path = env_text("CTYUN_MANAGER_DB", "./ctyun-manager.db")
    ctyun_mode = env_text("CTYUN_MANAGER_CTYUN_MODE", "openapi")
    session_secret = env_text("CTYUN_MANAGER_SESSION_SECRET", "change-this-session-secret")
    public_url = env_text("CTYUN_MANAGER_PUBLIC_URL", "http://127.0.0.1:8000")
    browser_headful = env_text("CTYUN_BROWSER_HEADFUL", "1").lower() not in {"0", "false", "no"}
    browser_vnc_enabled = env_text("CTYUN_BROWSER_VNC_ENABLED", env_text("CTYUN_ENABLE_BROWSER_VNC", "0")).lower() in {"1", "true", "yes", "on"}
    console_browser_fallback_enabled = env_text("CTYUN_CONSOLE_BROWSER_FALLBACK_ENABLED", "1").lower() not in {"0", "false", "no", "off"}
    browser_executable_path = env_text("CTYUN_BROWSER_EXECUTABLE_PATH", "")
    recharge_prewarm_enabled = env_text("CTYUN_RECHARGE_PREWARM_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    recharge_prewarm_startup_delay_seconds = max(0, int(env_text("CTYUN_RECHARGE_PREWARM_STARTUP_DELAY_SECONDS", "8")))
    recharge_prewarm_interval_seconds = max(600, int(env_text("CTYUN_RECHARGE_PREWARM_INTERVAL_SECONDS", "1800")))
    recharge_prewarm_workers = max(1, min(3, int(env_text("CTYUN_RECHARGE_PREWARM_WORKERS", "1"))))
    recharge_fast_order_enabled = env_text("CTYUN_RECHARGE_FAST_ORDER_ENABLED", "1").lower() not in {"0", "false", "no"}
    recharge_qr_cache_enabled = env_text("CTYUN_RECHARGE_QR_CACHE_ENABLED", "1").lower() not in {"0", "false", "no"}
    background_sync_enabled = env_text("CTYUN_BACKGROUND_SYNC_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    background_sync_seconds = max(300, int(env_text("CTYUN_BACKGROUND_SYNC_SECONDS", "1800")))
    finance_refresh_enabled = env_text("CTYUN_FINANCE_REFRESH_ENABLED", "1").lower() not in {"0", "false", "no", "off"}
    finance_refresh_seconds = max(1800, int(env_text("CTYUN_FINANCE_REFRESH_SECONDS", "1800")))
    finance_refresh_workers = max(1, min(5, int(env_text("CTYUN_FINANCE_REFRESH_WORKERS", "2"))))
    cookie_keepalive_enabled = env_text("CTYUN_COOKIE_KEEPALIVE_ENABLED", "1").lower() not in {"0", "false", "no", "off"}
    cookie_keepalive_seconds = max(1800, int(env_text("CTYUN_COOKIE_KEEPALIVE_SECONDS", "3600")))
    cookie_keepalive_workers = max(1, min(5, int(env_text("CTYUN_COOKIE_KEEPALIVE_WORKERS", "2"))))
    inventory_refresh_seconds = max(3600, int(env_text("CTYUN_INVENTORY_REFRESH_SECONDS", "86400")))
    inventory_workers = max(1, min(6, int(env_text("CTYUN_INVENTORY_WORKERS", "3"))))
    browser_page_refresh_seconds = max(300, int(env_text("CTYUN_BROWSER_PAGE_REFRESH_SECONDS", "600")))
    browser_session_idle_seconds = max(60, int(env_text("CTYUN_BROWSER_SESSION_IDLE_SECONDS", "600")))
    browser_session_cleanup_seconds = max(60, int(env_text("CTYUN_BROWSER_SESSION_CLEANUP_SECONDS", "120")))
    openapi_workers = max(1, min(12, int(env_text("CTYUN_OPENAPI_WORKERS", "6"))))
    openapi_timeout_seconds = max(5, min(60, int(env_text("CTYUN_OPENAPI_TIMEOUT_SECONDS", "20"))))
    default_region_ids = env_text("CTYUN_DEFAULT_REGION_IDS", "")
    region_endpoint = env_text("CTYUN_REGION_ENDPOINT", "https://ctecs-global.ctapi.ctyun.cn")
    region_list_path = env_text("CTYUN_REGION_LIST_PATH", "/v4/region/list-regions")
    ecs_endpoint = env_text("CTYUN_ECS_ENDPOINT", "https://ctecs-global.ctapi.ctyun.cn")
    ecs_list_path = env_text("CTYUN_ECS_LIST_PATH", "/v4/ecs/list-instances")
    eip_endpoint = env_text("CTYUN_EIP_ENDPOINT", "https://ctvpc-global.ctapi.ctyun.cn")
    eip_list_path = env_text("CTYUN_EIP_LIST_PATH", "/v4/eip/list")
    vpc_endpoint = env_text("CTYUN_VPC_ENDPOINT", "https://ctvpc-global.ctapi.ctyun.cn")
    vpc_list_path = env_text("CTYUN_VPC_LIST_PATH", "/v4/vpc/new-list")
    subnet_endpoint = env_text("CTYUN_SUBNET_ENDPOINT", "https://ctvpc-global.ctapi.ctyun.cn")
    subnet_list_path = env_text("CTYUN_SUBNET_LIST_PATH", "/v4/vpc/new-list-subnet")
    vip_endpoint = env_text("CTYUN_VIP_ENDPOINT", "https://ctvpc-global.ctapi.ctyun.cn")
    vip_list_path = env_text("CTYUN_VIP_LIST_PATH", "/v4/vpc/havip/list")
    ims_endpoint = env_text("CTYUN_IMS_ENDPOINT", "https://ctimage-global.ctapi.ctyun.cn")
    ims_list_path = env_text("CTYUN_IMS_LIST_PATH", "/v4/image/list")
    iam_endpoint = env_text("CTYUN_IAM_ENDPOINT", "https://ctiam-global.ctapi.ctyun.cn")
    iam_user_list_path = env_text("CTYUN_IAM_USER_LIST_PATH", "/v1/openapi/user/getUsers")

    @property
    def static_dir(self) -> Path:
        return Path(__file__).parent / "static"


settings = Settings()
