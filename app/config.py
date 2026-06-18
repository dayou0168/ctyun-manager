import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


class Settings:
    admin_user = os.getenv("CTYUN_MANAGER_ADMIN_USER", "admin")
    admin_password = os.getenv("CTYUN_MANAGER_ADMIN_PASSWORD", "change-me-now")
    master_key = os.getenv("CTYUN_MANAGER_MASTER_KEY", "")
    db_path = os.getenv("CTYUN_MANAGER_DB", "./ctyun-manager.db")
    ctyun_mode = os.getenv("CTYUN_MANAGER_CTYUN_MODE", "openapi")
    session_secret = os.getenv("CTYUN_MANAGER_SESSION_SECRET", "change-this-session-secret")
    public_url = os.getenv("CTYUN_MANAGER_PUBLIC_URL", "http://127.0.0.1:8000")
    browser_headful = os.getenv("CTYUN_BROWSER_HEADFUL", "1").lower() not in {"0", "false", "no"}
    browser_executable_path = os.getenv("CTYUN_BROWSER_EXECUTABLE_PATH", "")
    recharge_prewarm_enabled = os.getenv("CTYUN_RECHARGE_PREWARM_ENABLED", "1").lower() not in {"0", "false", "no"}
    recharge_prewarm_startup_delay_seconds = max(0, int(os.getenv("CTYUN_RECHARGE_PREWARM_STARTUP_DELAY_SECONDS", "8")))
    recharge_prewarm_interval_seconds = max(600, int(os.getenv("CTYUN_RECHARGE_PREWARM_INTERVAL_SECONDS", "1800")))
    recharge_prewarm_workers = max(1, min(3, int(os.getenv("CTYUN_RECHARGE_PREWARM_WORKERS", "1"))))
    recharge_fast_order_enabled = os.getenv("CTYUN_RECHARGE_FAST_ORDER_ENABLED", "1").lower() not in {"0", "false", "no"}
    recharge_qr_cache_enabled = os.getenv("CTYUN_RECHARGE_QR_CACHE_ENABLED", "1").lower() not in {"0", "false", "no"}
    background_sync_seconds = max(60, int(os.getenv("CTYUN_BACKGROUND_SYNC_SECONDS", "300")))
    finance_refresh_seconds = max(120, int(os.getenv("CTYUN_FINANCE_REFRESH_SECONDS", "300")))
    inventory_refresh_seconds = max(3600, int(os.getenv("CTYUN_INVENTORY_REFRESH_SECONDS", "86400")))
    inventory_workers = max(1, min(6, int(os.getenv("CTYUN_INVENTORY_WORKERS", "3"))))
    browser_page_refresh_seconds = max(300, int(os.getenv("CTYUN_BROWSER_PAGE_REFRESH_SECONDS", "600")))
    openapi_workers = max(1, min(12, int(os.getenv("CTYUN_OPENAPI_WORKERS", "6"))))
    openapi_timeout_seconds = max(5, min(60, int(os.getenv("CTYUN_OPENAPI_TIMEOUT_SECONDS", "20"))))
    default_region_ids = os.getenv("CTYUN_DEFAULT_REGION_IDS", "")
    region_endpoint = os.getenv("CTYUN_REGION_ENDPOINT", "https://ctecs-global.ctapi.ctyun.cn")
    region_list_path = os.getenv("CTYUN_REGION_LIST_PATH", "/v4/region/list-regions")
    ecs_endpoint = os.getenv("CTYUN_ECS_ENDPOINT", "https://ctecs-global.ctapi.ctyun.cn")
    ecs_list_path = os.getenv("CTYUN_ECS_LIST_PATH", "/v4/ecs/list-instances")
    eip_endpoint = os.getenv("CTYUN_EIP_ENDPOINT", "https://ctvpc-global.ctapi.ctyun.cn")
    eip_list_path = os.getenv("CTYUN_EIP_LIST_PATH", "/v4/eip/list")
    vpc_endpoint = os.getenv("CTYUN_VPC_ENDPOINT", "https://ctvpc-global.ctapi.ctyun.cn")
    vpc_list_path = os.getenv("CTYUN_VPC_LIST_PATH", "/v4/vpc/new-list")
    subnet_endpoint = os.getenv("CTYUN_SUBNET_ENDPOINT", "https://ctvpc-global.ctapi.ctyun.cn")
    subnet_list_path = os.getenv("CTYUN_SUBNET_LIST_PATH", "/v4/vpc/new-list-subnet")
    vip_endpoint = os.getenv("CTYUN_VIP_ENDPOINT", "https://ctvpc-global.ctapi.ctyun.cn")
    vip_list_path = os.getenv("CTYUN_VIP_LIST_PATH", "/v4/vpc/havip/list")
    ims_endpoint = os.getenv("CTYUN_IMS_ENDPOINT", "https://ctimage-global.ctapi.ctyun.cn")
    ims_list_path = os.getenv("CTYUN_IMS_LIST_PATH", "/v4/image/list")
    iam_endpoint = os.getenv("CTYUN_IAM_ENDPOINT", "https://ctiam-global.ctapi.ctyun.cn")
    iam_user_list_path = os.getenv("CTYUN_IAM_USER_LIST_PATH", "/v1/openapi/user/getUsers")

    @property
    def static_dir(self) -> Path:
        return Path(__file__).parent / "static"


settings = Settings()
