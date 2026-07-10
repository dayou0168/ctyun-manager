import json
import time
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..security import decrypt_text


class CtyunConsoleApiError(RuntimeError):
    pass


def _cookie_header(cookie_state_enc: str | None) -> str:
    state_text = decrypt_text(cookie_state_enc)
    if not state_text:
        raise CtyunConsoleApiError("账号没有保存网页登录状态，请先在平台完成一次天翼云网页登录。")
    try:
        state = json.loads(state_text)
    except json.JSONDecodeError as exc:
        raise CtyunConsoleApiError("账号网页登录状态格式异常，请重新登录天翼云。") from exc
    cookies = []
    for cookie in state.get("cookies") or []:
        if not isinstance(cookie, dict):
            continue
        domain = str(cookie.get("domain") or "")
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        if name and domain.endswith("ctyun.cn"):
            cookies.append(f"{name}={value}")
    if not cookies:
        raise CtyunConsoleApiError("账号没有可用的天翼云 cookie，请重新登录天翼云。")
    return "; ".join(cookies)


class CtyunConsoleApi:
    base_url = "https://www.ctyun.cn"

    def __init__(self, account: dict[str, Any]):
        self.account = account
        self.cookie_header = _cookie_header(account.get("cookie_state_enc"))

    def _request_json(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | list[Any] | None = None,
        referer: str = "https://www.ctyun.cn/console/expense/order/renew",
    ) -> dict[str, Any]:
        url = self.base_url + path
        if params:
            url += "?" + urlencode(params, doseq=True)
        data = None
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*",
            "Cookie": self.cookie_header,
            "Referer": referer,
        }
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, method=method.upper(), headers=headers)
        try:
            with urlopen(request, timeout=45) as response:
                text = response.read().decode("utf-8", "replace")
        except Exception as exc:
            raise CtyunConsoleApiError(f"天翼云控制台接口请求失败：{exc}") from exc
        try:
            data_obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CtyunConsoleApiError(f"天翼云控制台接口返回非 JSON：{text[:300]}") from exc
        self._raise_for_error(data_obj)
        return data_obj

    @staticmethod
    def _raise_for_error(data: dict[str, Any]) -> None:
        code = str(data.get("code") or data.get("statusCode") or "")
        reason = data.get("reason") or data.get("message") or data.get("description") or ""
        if code and code not in {"core.ok", "0", "800"}:
            raise CtyunConsoleApiError(str(reason or data)[:1000])
        if data.get("success") is False:
            raise CtyunConsoleApiError(str(reason or data)[:1000])

    def _unwrap(self, data: dict[str, Any]) -> Any:
        return data.get("data", data)

    @staticmethod
    def _return_obj(data: dict[str, Any]) -> Any:
        if not isinstance(data, dict):
            return data
        if "returnObj" in data:
            return data.get("returnObj")
        if "data" in data:
            return data.get("data")
        return data

    @staticmethod
    def _first_text(source: dict[str, Any], keys: list[str]) -> str:
        for key in keys:
            value = source.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""

    @classmethod
    def _renew_order_summary(cls, data: Any) -> dict[str, Any]:
        source = data if isinstance(data, dict) else {}
        success_raw = source.get("success")
        if isinstance(success_raw, str):
            success = success_raw.strip().lower() == "true"
        elif success_raw is None:
            success = None
        else:
            success = bool(success_raw)
        master_order_id = cls._first_text(source, ["cmOrderId", "masterOrderId", "orderId", "orderID"])
        order_no = cls._first_text(source, ["orderNo", "masterOrderNo"])
        reason = cls._first_text(source, ["reason", "message", "description"])
        pay_url = f"https://www.ctyun.cn/console/expense/order/pay?orderId={master_order_id}" if master_order_id else ""
        return {
            "success": success,
            "reason": reason,
            "order_no": order_no,
            "master_order_id": master_order_id,
            "pay_url": pay_url,
            "detail_url": f"https://www.ctyun.cn/console/expense/order/detail?orderId={master_order_id}" if master_order_id else "",
            "unpaid_url": "https://www.ctyun.cn/console/expense/order/unpaid",
        }

    @staticmethod
    def _order_amount(detail: dict[str, Any]) -> str:
        for key in ("finlePrice", "finalPrice", "payAmount", "totalPrice", "channelAmount", "cash", "amount"):
            value = detail.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""

    @staticmethod
    def _order_paid(status: str, status_name: str) -> bool:
        paid_statuses = {"2", "3", "14", "32"}
        paid_words = ("支付成功", "已支付", "交易成功", "已完成", "完成")
        return status in paid_statuses or any(word in status_name for word in paid_words)

    def list_renewable_instances(self, page_size: int = 50) -> list[dict[str, Any]]:
        page_size = max(1, min(int(page_size or 50), 50))
        rows: list[dict[str, Any]] = []
        page_no = 1
        while True:
            params = {
                "autoRenewStatus": "0",
                "createDateEnd": "",
                "createDateStart": "",
                "dueNoRenewal": "CUS_300_35_0001",
                "expireDateEnd": "",
                "expireDateStart": "",
                "ifRenew": "1",
                "isAutoToNeed": "CUS_300_1_0001",
                "isDemand": "true",
                "limit": str(page_size),
                "offset": str((page_no - 1) * page_size),
                "pageNo": str(page_no),
                "pageSize": str(page_size),
                "sceneType": "1",
                "statuses": "PUB_100_01_0001,PUB_100_01_0003",
            }
            data = self._unwrap(self._request_json("GET", "/v1/bcc/product/instance/List", params=params))
            page_rows = data.get("list") if isinstance(data, dict) else []
            if not isinstance(page_rows, list) or not page_rows:
                break
            rows.extend([row for row in page_rows if isinstance(row, dict)])
            try:
                total = int(data.get("total") or len(rows))
            except Exception:
                total = len(rows)
            if len(rows) >= total:
                break
            page_no += 1
            if page_no > 50:
                break
        return rows

    def resolve_renew_resources(self, instance_ids: list[str]) -> list[dict[str, Any]]:
        wanted = [str(item).strip() for item in instance_ids if str(item).strip()]
        if not wanted:
            raise CtyunConsoleApiError("请选择需要续订的云主机。")
        if len(wanted) > 50:
            raise CtyunConsoleApiError("天翼云手动批量续订一次最多支持 50 个资源。")
        rows = self.list_renewable_instances()
        by_org_id = {str(row.get("orgResourceId") or ""): row for row in rows}
        resolved: list[dict[str, Any]] = []
        missing: list[str] = []
        for instance_id in wanted:
            row = by_org_id.get(instance_id)
            if not row:
                missing.append(instance_id)
                continue
            renew_id = str(row.get("resourceId") or "").strip()
            if not renew_id:
                missing.append(instance_id)
                continue
            resolved.append(row)
        if missing:
            raise CtyunConsoleApiError(
                "官方续订管理没有返回这些云主机，可能不是包周期、已退订/释放，或 cookie 不是当前账号："
                + "、".join(missing[:5])
            )
        return resolved

    @staticmethod
    def _renew_payload(renew_rows: list[dict[str, Any]], month: int, by_year: bool) -> dict[str, Any]:
        month_value = max(1, min(int(month or 1), 36))
        return {
            "resourceIds": ";".join(str(row.get("resourceId") or "") for row in renew_rows),
            "month": month_value,
            "resourceType": "VM",
            "byYear": 1 if by_year else 0,
            "specRenewStatus": 0,
        }

    def renew_price(self, instance_ids: list[str], month: int = 1, by_year: bool = True) -> dict[str, Any]:
        rows = self.resolve_renew_resources(instance_ids)
        payload = {**self._renew_payload(rows, month, by_year), "sceneType": 1}
        data = self._unwrap(self._request_json("POST", "/v2/bcc/order/renew/getPrice", body=payload))
        return {
            "ok": True,
            "items": self._public_renew_rows(rows),
            "price": data,
            "payload": payload,
        }

    def renew_submit(self, instance_ids: list[str], month: int = 1, by_year: bool = True) -> dict[str, Any]:
        rows = self.resolve_renew_resources(instance_ids)
        payload = {**self._renew_payload(rows, month, by_year), "sceneType": 1}
        data = self._unwrap(
            self._request_json(
                "POST",
                "/v1/bcc/product/instance/renew/Submit",
                body={"data": payload},
                referer="https://www.ctyun.cn/console/expense/order/renew/manual",
            )
        )
        order = self._renew_order_summary(data)
        if order["success"] is False:
            raise CtyunConsoleApiError(order["reason"] or "天翼云续订订单创建失败。")
        return {
            "ok": True,
            "items": self._public_renew_rows(rows),
            "result": data,
            "order": order,
            "payload": payload,
            "submitted_at": int(time.time()),
        }

    def renew_order_status(self, master_order_id: str) -> dict[str, Any]:
        order_id = str(master_order_id or "").strip()
        if not order_id:
            raise CtyunConsoleApiError("缺少续订支付订单 ID。")
        referer = f"https://www.ctyun.cn/console/expense/order/pay?orderId={order_id}"
        errors: dict[str, str] = {}

        def request(name: str, path: str, params: dict[str, Any] | None = None) -> Any:
            try:
                return self._return_obj(self._request_json("GET", path, params=params or {"masterOrderId": order_id}, referer=referer))
            except CtyunConsoleApiError as exc:
                errors[name] = str(exc)
                return {}

        detail_obj = request("detail", "/v1/bcc/order/GetDetail", {"masterOrderId": order_id, "paymentPage": "1"})
        pay_type_obj = request("pay_type", "/v1/bcc/order/PayType")
        pay_detail_obj = request("pay_detail", "/v1/bcc/order/PayDetail")
        query_status_obj = request("query_status", "/v1/bcc/order/QueryOrderStatus")

        detail = detail_obj if isinstance(detail_obj, dict) else {}
        pay_detail = pay_detail_obj if isinstance(pay_detail_obj, dict) else {}
        query_status = query_status_obj if isinstance(query_status_obj, dict) else {}
        status = self._first_text(detail, ["status", "masterOrderStatus", "orderStatus"])
        status_name = self._first_text(detail, ["statusName", "masterOrderStatusName", "orderStatusName"])
        if not status_name:
            order_type = self._first_text(query_status, ["orderType", "orderTypeName"])
            status_name = order_type or ("待支付" if status in {"1", "22"} else "")
        order_no = self._first_text(detail, ["masterOrderNo", "orderNo"])
        amount = self._order_amount(detail) or self._order_amount(pay_detail)
        return {
            "ok": True,
            "master_order_id": order_id,
            "order_no": order_no,
            "status": status,
            "status_name": status_name,
            "paid": self._order_paid(status, status_name),
            "amount": amount,
            "pay_url": referer,
            "detail_url": f"https://www.ctyun.cn/console/expense/order/detail?orderId={order_id}",
            "unpaid_url": "https://www.ctyun.cn/console/expense/order/unpaid",
            "detail": detail_obj,
            "pay_type": pay_type_obj,
            "pay_detail": pay_detail_obj,
            "query_status": query_status_obj,
            "errors": errors,
            "checked_at": int(time.time()),
        }

    @staticmethod
    def _public_renew_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for row in rows:
            result.append({
                "instance_id": row.get("orgResourceId") or "",
                "renew_resource_id": row.get("resourceId") or "",
                "name": row.get("resourceName") or "",
                "zone_name": row.get("zoneName") or "",
                "status": row.get("stateName") or row.get("statusName") or "",
                "expire_date": row.get("expireDate") or "",
                "service_tag": row.get("serviceTag") or "",
                "resource_type": row.get("resourceType") or "",
            })
        return result
