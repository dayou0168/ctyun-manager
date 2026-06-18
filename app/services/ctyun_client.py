import base64
import hashlib
import hmac
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen

from ..config import settings
from ..security import decrypt_text


_region_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_region_cache_lock = threading.Lock()


class CtyunClientError(RuntimeError):
    pass


class CtyunClientSkipped(RuntimeError):
    pass


def friendly_error(data: dict[str, Any]) -> str:
    code = data.get("errorCode") or data.get("error") or data.get("code") or data.get("statusCode")
    message = data.get("message") or data.get("description") or data.get("details") or "未知错误"
    if code == "LiteEcs.RegionInfo.Empty" or "region info empty" in str(message).lower():
        return "资源池ID无效、为空，或当前接口不支持这个资源池。"
    return json.dumps(data, ensure_ascii=False)[:1000]


def compact(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [])}


def as_bool(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y", "是"}


def as_int(value: Any, default: int = 0) -> int:
    if value in (None, ""):
        return default
    return int(value)


def as_float(value: Any, default: float = 0) -> float:
    if value in (None, ""):
        return default
    return float(value)


def split_csv(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not value:
        return []
    text = str(value)
    for sep in ["\r\n", "\n", "，", "；", ";"]:
        text = text.replace(sep, ",")
    return [item.strip() for item in text.split(",") if item.strip()]


def first_present(*values: Any) -> str:
    for value in values:
        if value not in (None, ""):
            return str(value)
    return ""


def uuid_hyphen_variant(value: str) -> str:
    text = str(value or "").strip()
    if len(text) == 32 and all(ch in "0123456789abcdefABCDEF" for ch in text):
        return f"{text[0:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:32]}".lower()
    return ""


def error_text(exc: Exception) -> str:
    return str(exc).lower()


def is_instance_not_found(exc: Exception) -> bool:
    text = error_text(exc)
    return (
        "instance_not_found" in text
        or "instance.not_found" in text
        or "instance.notfound" in text
        or "instancenotfound" in text
        or "instance not found" in text
        or "not existed" in text
        or "not exist" in text
        or ("云主机" in text and "不存在" in text)
    )


class CtyunOpenApiClient:
    def __init__(self, account: dict[str, Any], require_region: bool = True):
        self.account = account
        self.ak = decrypt_text(account.get("ak_enc"))
        self.sk = decrypt_text(account.get("sk_enc"))
        self.region_ids = self._parse_region_ids(account.get("region", "")) or self._parse_region_ids(settings.default_region_ids)
        self._region_name_map: dict[str, str] | None = None
        if not self.ak or not self.sk:
            raise CtyunClientError("该账号缺少 AK/SK，无法调用正式 OpenAPI。")

    def _parse_region_ids(self, value: str | None) -> list[str]:
        if not value:
            return []
        for sep in ["\r\n", "\n", "，", ";", "；"]:
            value = value.replace(sep, ",")
        return [item.strip() for item in value.split(",") if item.strip()]

    def _hmac(self, key: bytes | str, value: str) -> bytes:
        if isinstance(key, str):
            key = key.encode("utf-8")
        return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()

    def _encode_query(self, query: dict[str, Any]) -> str:
        pairs = []
        for key, value in sorted(query.items(), key=lambda item: item[0]):
            if value is None or value == "":
                continue
            pairs.append(f"{key}={quote(str(value), safe='')}")
        return "&".join(pairs)

    def _auth_headers(self, body: bytes, query: dict[str, Any] | None = None) -> dict[str, str]:
        eop_date = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        request_id = str(uuid.uuid1())
        signed_headers = {
            "ctyun-eop-request-id": request_id,
            "eop-date": eop_date,
        }
        header_text = "".join(f"{name}:{signed_headers[name]}\n" for name in sorted(signed_headers))
        query_text = self._encode_query(query or {})
        body_hash = hashlib.sha256(body).hexdigest()
        signature_source = f"{header_text}\n{query_text}\n{body_hash}"
        k_time = self._hmac(self.sk, eop_date)
        k_ak = self._hmac(k_time, self.ak)
        k_date = self._hmac(k_ak, eop_date[:8])
        signature = base64.b64encode(self._hmac(k_date, signature_source)).decode("ascii")
        signed_names = ";".join(sorted(signed_headers))
        return {
            **signed_headers,
            "Eop-Authorization": f"{self.ak} Headers={signed_names} Signature={signature}",
            "Content-Type": "application/json",
        }

    def _request(self, endpoint: str, path: str, body: dict[str, Any] | None = None, method: str = "POST") -> dict[str, Any]:
        if not endpoint or not path:
            raise CtyunClientSkipped("接口未配置，已跳过该模块。")
        method = method.upper()
        query = (body or {}) if method == "GET" else {}
        payload = b"" if method == "GET" else json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
        url = urljoin(endpoint.rstrip("/") + "/", path.lstrip("/"))
        if method == "GET" and query:
            url = f"{url}?{self._encode_query(query)}"
        request = Request(url, data=payload if method != "GET" else None, headers=self._auth_headers(payload, query), method=method)
        try:
            with urlopen(request, timeout=settings.openapi_timeout_seconds) as response:
                text = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise CtyunClientError(f"天翼云 OpenAPI HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise CtyunClientError(f"无法连接天翼云 OpenAPI: {exc.reason}") from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CtyunClientError(f"天翼云 OpenAPI 返回非 JSON: {text[:500]}") from exc
        code = str(data.get("statusCode", data.get("code", "")))
        if code and code not in {"800", "200", "0"}:
            raise CtyunClientError(f"天翼云 OpenAPI 返回错误: {friendly_error(data)}")
        return data

    def _items(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        obj = data.get("returnObj", data.get("data", data))
        if isinstance(obj, list):
            nested: list[dict[str, Any]] = []
            for entry in obj:
                if not isinstance(entry, dict):
                    continue
                child_found = False
                for key in [
                    "eips", "vpcs", "subnets", "images", "results", "records", "items",
                    "zoneList", "securityGroups", "routeTables", "acls",
                ]:
                    value = entry.get(key)
                    if isinstance(value, list):
                        nested.extend(item for item in value if isinstance(item, dict))
                        child_found = True
                if not child_found:
                    nested.append(entry)
            return nested
        if isinstance(obj, dict):
            for key in [
                "regionList", "results", "eips", "vpcs", "subnets", "images", "records",
                "rows", "list", "items", "data", "zoneList", "securityGroups",
                "routeTables", "acls",
            ]:
                value = obj.get(key)
                if isinstance(value, list):
                    return value
        return []

    def _total_pages(self, data: dict[str, Any]) -> int:
        candidates = [data, data.get("returnObj"), data.get("data")]
        for candidate in candidates:
            if isinstance(candidate, dict):
                value = candidate.get("totalPage") or candidate.get("totalPages")
                if value is not None:
                    try:
                        return max(1, int(value))
                    except (TypeError, ValueError):
                        pass
        return 1

    def _regions(self) -> list[dict[str, Any]]:
        cache_key = self.ak or ""
        now = time.monotonic()
        with _region_cache_lock:
            cached = _region_cache.get(cache_key)
            if cached and cached[0] > now:
                return cached[1]
        regions = self.list_regions()
        with _region_cache_lock:
            _region_cache[cache_key] = (now + 600, regions)
        return regions

    def _region_ids_for_scan(self) -> list[str]:
        if self.region_ids:
            return self.region_ids
        return [region["regionID"] for region in self._regions() if region.get("regionID")]

    def _region_name(self, region_id: str) -> str:
        if self._region_name_map is None:
            try:
                self._region_name_map = {
                    region["regionID"]: region.get("regionName", region["regionID"])
                    for region in self._regions()
                    if region.get("regionID")
                }
            except Exception:
                self._region_name_map = {}
        return self._region_name_map.get(region_id, region_id)

    def _extract_address(self, item: dict[str, Any], wanted: str) -> str:
        addresses = item.get("addresses") if isinstance(item.get("addresses"), list) else []
        for group in addresses:
            for address in group.get("addressList", []) if isinstance(group, dict) else []:
                addr_type = str(address.get("type", "")).lower()
                if wanted == "public" and addr_type in {"floating", "public", "internet"}:
                    return str(address.get("addr", ""))
                if wanted == "private" and addr_type in {"fixed", "private", "intranet"}:
                    return str(address.get("addr", ""))
        return ""

    def _normalize(self, item: dict[str, Any], fallback_type: str, region_id: str) -> dict[str, Any]:
        actual_region_id = first_present(
            item.get("regionID"),
            item.get("regionId"),
            item.get("region_id"),
            item.get("regionUUID"),
            item.get("regionUuid"),
            region_id,
        )
        flavor = item.get("flavor") if isinstance(item.get("flavor"), dict) else {}
        os_info = item.get("os") if isinstance(item.get("os"), dict) else {}
        network_cards = item.get("networkCardList") if isinstance(item.get("networkCardList"), list) else []
        first_card = network_cards[0] if network_cards and isinstance(network_cards[0], dict) else {}
        fixed_ip_list = item.get("fixedIpList") if isinstance(item.get("fixedIpList"), list) else []
        instance_info = item.get("instanceInfo") if isinstance(item.get("instanceInfo"), list) else []
        network_info = item.get("networkInfo") if isinstance(item.get("networkInfo"), list) else []
        image_visibility = (
            item.get("imageVisibilityCode")
            if item.get("imageVisibilityCode") is not None
            else item.get("imageVisibility")
        )
        if image_visibility in (None, ""):
            image_visibility = item.get("visibility", "")
        if fallback_type == "ecs":
            rid = first_present(
                item.get("instanceID"), item.get("instanceId"), item.get("instance_id"),
                item.get("deviceUUID"), item.get("deviceUuid"), item.get("resourceID"),
                item.get("resourceId"), item.get("uuid"), item.get("id"), item.get("ID"),
            )
        else:
            rid = first_present(
                item.get("id"), item.get("uuid"), item.get("ID"), item.get(f"{fallback_type}ID"),
                item.get("resourceID"), item.get("instanceID"), item.get("eipID"),
                item.get("vpcID"), item.get("subnetID"), item.get("imageID"),
                item.get("imageUUID"), item.get("imageUuid"), item.get("image_uuid"),
            )
        name = (
            item.get("name") or item.get("displayName") or item.get("instanceName")
            or item.get("eipName") or item.get("vpcName") or item.get("imageName")
            or item.get("nameZh") or item.get("nameEn") or item.get("securityGroupName") or rid
        )
        public_ip = (
            item.get("public_ip") or item.get("publicIP") or item.get("floatingIP")
            or item.get("floatingIp") or item.get("eipAddress") or self._extract_address(item, "public")
        )
        private_ip = (
            item.get("private_ip") or item.get("privateIP") or item.get("privateIp")
            or first_card.get("IPv4Address") or (fixed_ip_list[0] if fixed_ip_list else "")
            or self._extract_address(item, "private")
        )
        normalized = {
            "id": str(rid or ""),
            "name": str(name or ""),
            "status": str(
                item.get("status")
                or item.get("state")
                or item.get("instanceStatus")
                or item.get("imageStatus")
                or ""
            ),
            "region": actual_region_id,
            "region_name": self._region_name(actual_region_id),
            "billing_mode": str(item.get("billingMode") or item.get("cycleType") or item.get("chargeType") or ""),
            "spec": item.get("spec") or item.get("flavorName") or flavor.get("flavorName") or "",
            "private_ip": private_ip or "",
            "public_ip": public_ip or "",
            "ip": item.get("ip") or item.get("ipv4") or item.get("eipAddress") or item.get("floatingIP") or "",
            "bandwidth_mbps": item.get("bandwidth_mbps") or item.get("bandwidth") or "",
            "cidr": item.get("cidr") or item.get("CIDR") or "",
            "vpc_id": item.get("vpcID") or item.get("vpcId") or "",
            "subnet_id": item.get("subnetID") or item.get("subnetId") or "",
            "network_card_id": first_card.get("networkCardID") or item.get("networkInterfaceID") or "",
            "bound_instances": ", ".join(filter(None, [x.get("instanceName") or x.get("id") for x in instance_info if isinstance(x, dict)])),
            "bound_eips": ", ".join(filter(None, [x.get("eipID") for x in network_info if isinstance(x, dict)])),
            "image_type": item.get("imageType") or item.get("image_type") or "",
            "os": (
                os_info.get("nameZh") or os_info.get("nameEn") or os_info.get("osType")
                or os_info.get("osName") or (item.get("os") if not isinstance(item.get("os"), dict) else "")
                or item.get("osType") or item.get("osDistro") or item.get("osVersion") or ""
            ),
            "visibility": str(image_visibility),
            "source_user": item.get("sourceAccountID") or item.get("sourceUser") or "",
            "destination_user": item.get("destinationAccountID") or item.get("destinationUser") or "",
        }
        return {**item, **normalized}

    def _list(
        self,
        endpoint: str,
        path: str,
        resource_type: str,
        method: str = "POST",
        extra_params: dict[str, Any] | None = None,
        variants: list[dict[str, Any]] | None = None,
        include_paging: bool = True,
    ) -> list[dict[str, Any]]:
        if not endpoint or not path:
            raise CtyunClientSkipped("接口未配置，已跳过该模块。")
        paths = [p.strip() for p in path.replace("，", ",").split(",") if p.strip()]
        if not paths:
            raise CtyunClientSkipped("接口 path 未配置，已跳过该模块。")
        region_ids = self._region_ids_for_scan()
        if not region_ids:
            raise CtyunClientError("没有查询到可用资源池。请检查 AK/SK 权限，或手动填写资源池ID列表。")

        results: list[dict[str, Any]] = []
        errors: list[str] = []
        seen: set[str] = set()
        variants = variants or [{}]
        try:
            self._region_name_map = {
                region["regionID"]: region.get("regionName", region["regionID"])
                for region in self._regions()
                if region.get("regionID")
            }
        except Exception:
            self._region_name_map = {region_id: region_id for region_id in region_ids}

        def scan(path_item: str, region_id: str, variant: dict[str, Any]) -> tuple[list[dict[str, Any]], int, list[str]]:
            scanned: list[dict[str, Any]] = []
            scan_errors: list[str] = []
            successes = 0
            page = 1
            total_pages = 1
            while page <= total_pages and page <= 200:
                try:
                    params = {"regionID": region_id, **(extra_params or {}), **variant}
                    if include_paging:
                        params.update({"pageNo": page, "pageSize": 50})
                        if resource_type in {"vpc", "subnet"}:
                            params["pageNumber"] = params.pop("pageNo")
                        if resource_type == "eip":
                            params["page"] = page
                    data = self._request(endpoint, path_item, params, method=method)
                    successes += 1
                    total_pages = self._total_pages(data) if include_paging else 1
                    for item in self._items(data):
                        normalized = self._normalize(item, resource_type, region_id)
                        if normalized.get("id"):
                            normalized["_api_path"] = path_item
                            scanned.append(normalized)
                    page += 1
                except CtyunClientSkipped:
                    raise
                except CtyunClientError as exc:
                    scan_errors.append(f"{path_item} {region_id}: {exc}")
                    break
            return scanned, successes, scan_errors

        tasks = [
            (path_item, region_id, variant)
            for path_item in paths
            for region_id in region_ids
            for variant in variants
        ]
        success_count = 0
        with ThreadPoolExecutor(max_workers=min(settings.openapi_workers, len(tasks))) as executor:
            futures = [executor.submit(scan, *task) for task in tasks]
            for future in as_completed(futures):
                scanned, successes, scan_errors = future.result()
                success_count += successes
                errors.extend(scan_errors)
                for normalized in scanned:
                    dedupe_key = f"{normalized.get('id')}:{normalized.get('region')}"
                    if dedupe_key not in seen:
                        results.append(normalized)
                        seen.add(dedupe_key)
        if success_count == 0 and errors:
            raise CtyunClientError("；".join(errors))
        return results

    def list_regions(self) -> list[dict[str, Any]]:
        data = self._request(settings.region_endpoint, settings.region_list_path, {}, method="GET")
        regions = []
        for item in self._items(data):
            if not isinstance(item, dict):
                continue
            region_id = item.get("regionID") or item.get("regionId") or item.get("id")
            region_name = item.get("regionName") or item.get("name") or item.get("region")
            if region_id:
                regions.append({"regionID": str(region_id), "regionName": str(region_name or region_id), **item})
        return regions

    def list_ecs(self) -> list[dict[str, Any]]:
        return self._list(settings.ecs_endpoint, settings.ecs_list_path, "ecs")

    def list_eips(self) -> list[dict[str, Any]]:
        return self._list(settings.eip_endpoint, settings.eip_list_path, "eip", extra_params={"clientToken": str(uuid.uuid4())})

    def list_vpcs(self) -> list[dict[str, Any]]:
        return self._list(settings.vpc_endpoint, settings.vpc_list_path, "vpc", method="GET", extra_params={"projectID": "0"})

    def list_subnets(self) -> list[dict[str, Any]]:
        return self._list(settings.subnet_endpoint, settings.subnet_list_path, "subnet", method="GET")

    def list_vips(self) -> list[dict[str, Any]]:
        return self._list(
            settings.vip_endpoint,
            settings.vip_list_path,
            "vip",
            extra_params={"clientToken": str(uuid.uuid4()), "projectID": "0"},
            include_paging=False,
        )

    def list_images(self) -> list[dict[str, Any]]:
        return self._list(
            settings.ims_endpoint,
            settings.ims_list_path,
            "image",
            method="GET",
            variants=[{"imageVisibilityCode": value} for value in (1, 0, 2)]
            + [{"imageType": value} for value in ("standard", "public", "private", "shared")]
            + [{}],
        )

    def list_security_groups(self) -> list[dict[str, Any]]:
        return self._list(
            settings.vpc_endpoint,
            "/v4/vpc/new-query-security-groups",
            "security_group",
            method="GET",
        )

    def list_route_tables(self) -> list[dict[str, Any]]:
        return self._list(
            settings.vpc_endpoint,
            "/v4/vpc/route-table/new-list",
            "route_table",
            method="GET",
        )

    def list_acls(self) -> list[dict[str, Any]]:
        return self._list(
            settings.vpc_endpoint,
            "/v4/acl/new-list",
            "acl",
            method="GET",
        )

    def list_zones(self, region_id: str) -> list[dict[str, Any]]:
        data = self._request(
            settings.ecs_endpoint,
            "/v4/region/get-zones",
            {"regionID": region_id},
            method="GET",
        )
        return self._items(data)

    def list_flavors(self, region_id: str, az_name: str = "", available_only: bool = False) -> list[dict[str, Any]]:
        body = {"regionID": region_id, "azName": az_name}
        if available_only:
            body.update({
                "availableOnly": True,
                "onlyAvailable": True,
                "showSoldOut": False,
                "showSoldout": False,
                "includeSoldOut": False,
                "includeSoldout": False,
                "filterSoldOut": True,
                "isShowAll": False,
            })
        try:
            data = self._request(
                settings.ecs_endpoint,
                "/v4/common/get-ecs-flavors",
                compact(body),
                method="GET",
            )
        except CtyunClientError:
            if not available_only:
                raise
            data = self._request(
                settings.ecs_endpoint,
                "/v4/common/get-ecs-flavors",
                compact({"regionID": region_id, "azName": az_name}),
                method="GET",
            )
        return self._items(data)

    def list_security_groups_for_region(self, region_id: str, vpc_id: str = "") -> list[dict[str, Any]]:
        data = self._request(
            settings.vpc_endpoint,
            "/v4/vpc/new-query-security-groups",
            compact({
                "regionID": region_id,
                "vpcID": vpc_id,
                "pageNo": 1,
                "pageNumber": 1,
                "pageSize": 50,
            }),
            method="GET",
        )
        return self._items(data)

    def list_keypairs(self, region_id: str) -> list[dict[str, Any]]:
        data = self._request(
            settings.ecs_endpoint,
            "/v4/ecs/keypair/details",
            {"regionID": region_id, "pageNo": 1, "pageSize": 50, "projectID": "0"},
            method="POST",
        )
        return self._items(data)

    def get_account_id(self) -> str:
        data = self._request(
            settings.iam_endpoint,
            settings.iam_user_list_path,
            {"pageNum": 1, "pageSize": 100},
            method="POST",
        )
        obj = data.get("returnObj")
        users = obj.get("result", []) if isinstance(obj, dict) else []
        if not isinstance(users, list):
            return ""
        root_user = next(
            (
                item
                for item in users
                if isinstance(item, dict) and str(item.get("isRoot", "0")) == "1"
            ),
            None,
        )
        selected = root_user or next((item for item in users if isinstance(item, dict)), None)
        if not selected:
            return ""
        return str(selected.get("accountId") or selected.get("accountID") or "")

    def _region_for_action(self, payload: dict[str, Any]) -> str:
        region_id = payload.get("regionID") or payload.get("region") or payload.get("region_id")
        if region_id:
            return str(region_id)
        if self.region_ids:
            return self.region_ids[0]
        raise CtyunClientError("该操作缺少 regionID。")

    def _post_action(self, endpoint: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request(endpoint, path, body, method="POST")

    def _ecs_instance_id_candidates(self, payload: dict[str, Any]) -> list[str]:
        cached = payload.get("_cached") if isinstance(payload.get("_cached"), dict) else {}
        candidates = [
            payload.get("instanceID"),
            payload.get("instanceId"),
            payload.get("instance_id"),
            cached.get("instanceID"),
            cached.get("instanceId"),
            cached.get("instance_id"),
            cached.get("deviceUUID"),
            cached.get("deviceUuid"),
            cached.get("uuid"),
            cached.get("id"),
            cached.get("ID"),
            cached.get("resourceID"),
            cached.get("resourceId"),
            payload.get("resource_id"),
        ]
        result: list[str] = []
        for value in candidates:
            text = str(value or "").strip()
            if text and text not in result:
                result.append(text)
            variant = uuid_hyphen_variant(text)
            if variant and variant not in result:
                result.append(variant)
        return result

    def _network_interface_id_candidates(self, payload: dict[str, Any]) -> list[str]:
        cached = payload.get("_cached") if isinstance(payload.get("_cached"), dict) else {}
        network_cards = cached.get("networkCardList") if isinstance(cached.get("networkCardList"), list) else []
        first_card = network_cards[0] if network_cards and isinstance(network_cards[0], dict) else {}
        candidates = [
            payload.get("networkInterfaceID"),
            payload.get("networkInterfaceId"),
            payload.get("networkCardID"),
            payload.get("networkCardId"),
            payload.get("portID"),
            payload.get("portId"),
            cached.get("networkInterfaceID"),
            cached.get("networkInterfaceId"),
            cached.get("network_card_id"),
            cached.get("networkCardID"),
            cached.get("networkCardId"),
            cached.get("portID"),
            cached.get("portId"),
            first_card.get("networkInterfaceID"),
            first_card.get("networkInterfaceId"),
            first_card.get("networkCardID"),
            first_card.get("networkCardId"),
            first_card.get("portID"),
            first_card.get("portId"),
            first_card.get("id"),
        ]
        result: list[str] = []
        for value in candidates:
            text = str(value or "").strip()
            if text and text not in result:
                result.append(text)
        return result

    def _live_network_interface_id_candidates(self, region_id: str, instance_id: str, payload: dict[str, Any]) -> list[str]:
        if not region_id or not instance_id:
            return []
        vpc_id = payload.get("vpcID") or payload.get("vpcId") or payload.get("vpc_id")
        subnet_id = payload.get("subnetID") or payload.get("subnetId") or payload.get("subnet_id")
        variants = [{"deviceID": instance_id}, {"instanceID": instance_id}, {"deviceId": instance_id}]
        if vpc_id:
            variants.append({"deviceID": instance_id, "vpcID": vpc_id})
        if subnet_id:
            variants.append({"deviceID": instance_id, "subnetID": subnet_id})
        queries = [
            compact({"regionID": region_id, "pageNo": 1, "pageNumber": 1, "pageSize": 50, **variant})
            for variant in variants
        ]
        candidates: list[tuple[int, str]] = []
        for endpoint, path in [
            (settings.ecs_endpoint, "/v4/ecs/ports/list"),
            (settings.ecs_endpoint, "/v4/ports/list"),
            (settings.vpc_endpoint, "/v4/ports/list"),
            (settings.vpc_endpoint, "/v4/vpc/ports/list"),
        ]:
            for query in queries:
                try:
                    data = self._request(endpoint, path, query, method="GET")
                except CtyunClientError:
                    continue
                for item in self._items(data):
                    if not isinstance(item, dict):
                        continue
                    nic_id = (
                        item.get("networkInterfaceID") or item.get("networkInterfaceId")
                        or item.get("networkCardID") or item.get("networkCardId")
                        or item.get("portID") or item.get("portId") or item.get("id")
                    )
                    text = str(nic_id or "").strip()
                    if not text:
                        continue
                    role = str(item.get("role") or item.get("isMaster") or item.get("type") or "").lower()
                    score = 0 if role in {"1", "true", "master", "main", "primary"} else 1
                    candidates.append((score, text))
                if candidates:
                    break
            if candidates:
                break
        result: list[str] = []
        for _score, value in sorted(candidates, key=lambda item: item[0]):
            if value not in result:
                result.append(value)
        return result

    def _ecs_match_values(self, payload: dict[str, Any]) -> tuple[set[str], set[str]]:
        cached = payload.get("_cached") if isinstance(payload.get("_cached"), dict) else {}
        id_values: set[str] = set()
        name_values: set[str] = set()
        for value in [
            payload.get("resource_id"),
            payload.get("instanceID"),
            payload.get("instanceId"),
            cached.get("resourceID"),
            cached.get("resourceId"),
            cached.get("instanceID"),
            cached.get("instanceId"),
            cached.get("deviceUUID"),
            cached.get("deviceUuid"),
            cached.get("uuid"),
            cached.get("id"),
            cached.get("ID"),
        ]:
            text = str(value or "").strip()
            if text:
                id_values.add(text)
                variant = uuid_hyphen_variant(text)
                if variant:
                    id_values.add(variant)
        for value in [
            payload.get("instanceName"),
            payload.get("displayName"),
            cached.get("instanceName"),
            cached.get("displayName"),
            cached.get("name"),
        ]:
            text = str(value or "").strip()
            if text:
                name_values.add(text)
        return id_values, name_values

    def _resolve_ecs_instance_id(self, payload: dict[str, Any]) -> str:
        region_id = self._region_for_action(payload)
        id_values, name_values = self._ecs_match_values(payload)
        if not id_values and not name_values:
            return ""

        def scan(endpoint: str, path: str, method: str) -> str:
            name_matches: list[str] = []
            page = 1
            total_pages = 1
            while page <= total_pages and page <= 20:
                params = {"regionID": region_id, "pageNo": page, "page": page, "pageSize": 50}
                data = self._request(endpoint, path, params, method=method)
                total_pages = self._total_pages(data)
                for item in self._items(data):
                    if not isinstance(item, dict):
                        continue
                    instance_id = first_present(
                        item.get("instanceID"),
                        item.get("instanceId"),
                        item.get("instance_id"),
                        item.get("deviceUUID"),
                        item.get("deviceUuid"),
                    )
                    item_ids = {
                        str(value).strip()
                        for value in [
                            item.get("resourceID"),
                            item.get("resourceId"),
                            item.get("instanceID"),
                            item.get("instanceId"),
                            item.get("deviceUUID"),
                            item.get("deviceUuid"),
                            item.get("uuid"),
                            item.get("id"),
                            item.get("ID"),
                        ]
                        if str(value or "").strip()
                    }
                    for value in list(item_ids):
                        variant = uuid_hyphen_variant(value)
                        if variant:
                            item_ids.add(variant)
                    if instance_id and id_values.intersection(item_ids):
                        return instance_id
                    item_names = {
                        str(value).strip()
                        for value in [
                            item.get("instanceName"),
                            item.get("displayName"),
                            item.get("name"),
                        ]
                        if str(value or "").strip()
                    }
                    if instance_id and name_values.intersection(item_names) and instance_id not in name_matches:
                        name_matches.append(instance_id)
                page += 1
            return name_matches[0] if len(name_matches) == 1 else ""

        requests = [
            (settings.ecs_endpoint, settings.ecs_list_path, "POST"),
            (settings.ecs_endpoint, "/v4/monitor/query-ecs-list", "GET"),
        ]
        errors: list[str] = []
        for endpoint, path, method in requests:
            if not endpoint or not path:
                continue
            try:
                resolved = scan(endpoint, path, method)
                if resolved:
                    return resolved
            except CtyunClientError as exc:
                errors.append(str(exc))
        return ""

    def _post_ecs_instance_action(
        self,
        path: str,
        payload: dict[str, Any],
        make_body,
    ) -> dict[str, Any]:
        candidates = self._ecs_instance_id_candidates(payload)
        if not candidates:
            raise CtyunClientError("云主机操作缺少 instanceID。")
        errors: list[str] = []
        for instance_id in candidates:
            try:
                return self._post_action(settings.ecs_endpoint, path, make_body(instance_id))
            except CtyunClientError as exc:
                errors.append(f"{instance_id}: {exc}")
                if not is_instance_not_found(exc):
                    raise
        resolved = self._resolve_ecs_instance_id(payload)
        if resolved and resolved not in candidates:
            try:
                return self._post_action(settings.ecs_endpoint, path, make_body(resolved))
            except CtyunClientError as exc:
                errors.append(f"{resolved}: {exc}")
                if not is_instance_not_found(exc):
                    raise
        raise CtyunClientError("云主机不存在或缓存的实例ID已失效：" + "；".join(errors[:3]))

    def query_eip_create_price(self, payload: dict[str, Any]) -> dict[str, Any]:
        region_id = self._region_for_action(payload)
        body = compact({
            "clientToken": str(uuid.uuid4()),
            "regionID": region_id,
            "projectID": payload.get("projectID") or "0",
            "cycleType": payload.get("cycleType") or "on_demand",
            "cycleCount": as_int(payload.get("cycleCount")) if payload.get("cycleCount") not in (None, "") else None,
            "name": payload.get("name") or payload.get("eipName") or "eip-price-preview",
            "bandwidth": as_int(payload.get("bandwidth"), 5),
            "bandwidthID": payload.get("bandwidthID"),
            "demandBillingType": payload.get("demandBillingType") or "bandwidth",
        })
        errors: list[str] = []
        for path in ["/v4/eip/querycreateprice", "/v4/eip/query-create-price"]:
            try:
                return self._post_action(settings.eip_endpoint, path, body)
            except CtyunClientError as exc:
                errors.append(f"{path}: {exc}")
        raise CtyunClientError("弹性IP询价接口暂不可用：" + "；".join(errors))

    def _ecs_create_body(self, payload: dict[str, Any], region_id: str) -> dict[str, Any]:
        body = compact({
            "clientToken": str(uuid.uuid4()),
            "regionID": region_id,
            "azName": payload.get("azName") or "random",
            "instanceName": payload.get("instanceName") or "ecs-price-preview",
            "displayName": payload.get("displayName") or payload.get("instanceName") or "ecs-price-preview",
            "flavorID": payload.get("flavorID"),
            "flavorName": payload.get("flavorName"),
            "imageType": as_int(payload.get("imageType"), 1),
            "imageID": payload.get("imageID"),
            "bootDiskType": payload.get("bootDiskType") or "SSD",
            "bootDiskSize": as_int(payload.get("bootDiskSize"), 40),
            "vpcID": payload.get("vpcID"),
            "onDemand": as_bool(payload.get("onDemand"), True),
            "extIP": payload.get("extIP") or "0",
            "bandwidth": as_int(payload.get("bandwidth"), 1),
            "userPassword": payload.get("userPassword"),
            "keyPairID": payload.get("keyPairID"),
            "cycleCount": as_int(payload.get("cycleCount")) if payload.get("cycleCount") not in (None, "") else None,
            "cycleType": payload.get("cycleType"),
            "autoRenewStatus": as_int(payload.get("autoRenewStatus"), 0),
            "projectID": payload.get("projectID") or "0",
            "userData": payload.get("userData"),
            "payVoucherPrice": as_float(payload.get("payVoucherPrice"), 0),
            "monitorService": as_bool(payload.get("monitorService"), True),
            "securityProduct": payload.get("securityProduct"),
            "demandBillingType": payload.get("demandBillingType"),
            "eipID": payload.get("eipID"),
        })
        sec_groups = split_csv(payload.get("secGroupList"))
        if sec_groups:
            body["secGroupList"] = sec_groups
        subnet_id = payload.get("subnetID")
        if subnet_id:
            body["networkCardList"] = [{"subnetID": subnet_id, "isMaster": True}]
        return body

    def query_ecs_create_price(self, payload: dict[str, Any]) -> dict[str, Any]:
        region_id = self._region_for_action(payload)
        body = self._ecs_create_body(payload, region_id)
        errors: list[str] = []
        try:
            return self.query_ecs_new_order_price(payload)
        except CtyunClientError as exc:
            message = str(exc)
            lowered = message.lower()
            errors.append(f"new-order-price: {message}")
            if as_bool(payload.get("onDemand"), True) and (
                "Unknown.OrderCheck.UserForbiddenOnDemand" in message
                or "Ecs.OrderCheck.UserForbiddenOnDemand" in message
                or "user not allowed place ondemand order" in lowered
                or "用户不允许订购按需类订单" in message
                or "用户详情信息不符预期" in message
            ):
                raise CtyunClientError("当前账号暂不支持按量计费询价/下单，请切换为包年包月后再询价。")
            if (
                "FlavorSoldOut" in message
                or "flavor sold out" in lowered
                or (
                    ("该规格" in message or "云主机规格" in message)
                    and ("售罄" in message or "售完" in message or "无库存" in message)
                    and "售罄信息检查失败" not in message
                    and "EbsSoldOut" not in message
                )
            ):
                raise
        for path in [
            "/v4/ecs/querycreateprice",
            "/v4/ecs/query-create-price",
            "/v4/ecs/create-instance-price",
            "/v4/ecs/query-price",
            "/v4/ecs/query-instance-price",
        ]:
            try:
                return self._post_action(settings.ecs_endpoint, path, body)
            except CtyunClientError as exc:
                errors.append(f"{path}: {exc}")
        raise CtyunClientError("云主机询价接口暂不可用：" + "；".join(errors))

    def query_ecs_new_order_price(self, payload: dict[str, Any]) -> dict[str, Any]:
        region_id = self._region_for_action(payload)
        flavor_name = payload.get("flavorName") or payload.get("specName")
        image_id = payload.get("imageUUID") or payload.get("imageID")
        if not flavor_name or not image_id:
            raise CtyunClientError("云主机询价缺少 flavorName/specName 或 imageID。")
        on_demand = as_bool(payload.get("onDemand"), True)
        body = compact({
            "regionID": region_id,
            "resourceType": "VM",
            "count": as_int(payload.get("count"), 1),
            "onDemand": on_demand,
            "cycleType": payload.get("cycleType") if not on_demand else None,
            "cycleCount": as_int(payload.get("cycleCount"), 1) if not on_demand else None,
            "flavorName": flavor_name,
            "imageUUID": image_id,
            "sysDiskType": payload.get("sysDiskType") or payload.get("bootDiskType") or "SSD",
            "sysDiskSize": as_int(payload.get("sysDiskSize") or payload.get("bootDiskSize"), 40),
            "bandwidth": as_int(payload.get("bandwidth"), 1) if str(payload.get("extIP") or "0") == "1" else None,
        })
        errors: list[str] = []
        for path in ["/v4/order/new-query-price", "/v4/new-order/query-price"]:
            try:
                return self._post_action(settings.ecs_endpoint, path, body)
            except CtyunClientError as exc:
                errors.append(f"{path}: {exc}")
        raise CtyunClientError("云主机新购询价接口暂不可用：" + "；".join(errors))

    def query_ecs_vnc_details(self, payload: dict[str, Any]) -> dict[str, Any]:
        region_id = self._region_for_action(payload)
        instance_ids = self._ecs_instance_id_candidates(payload)
        if not instance_ids:
            raise CtyunClientError("获取远程登录地址缺少 instanceID。")
        errors: list[str] = []
        for instance_id in instance_ids:
            query = {"regionID": region_id, "instanceID": instance_id}
            for path in ["/v4/ecs/vnc/details", "/v4/ecs/lite/vnc/details"]:
                try:
                    data = self._request(settings.ecs_endpoint, path, query, method="GET")
                    obj = data.get("returnObj") if isinstance(data.get("returnObj"), dict) else {}
                    token = (
                        obj.get("token")
                        or obj.get("url")
                        or obj.get("vncUrl")
                        or obj.get("vncURL")
                        or data.get("token")
                    )
                    if not token:
                        raise CtyunClientError(f"{path}: 官方接口未返回 VNC 地址。")
                    return {
                        "regionID": region_id,
                        "instanceID": instance_id,
                        "path": path,
                        "url": str(token),
                        "raw": data,
                    }
                except CtyunClientError as exc:
                    errors.append(f"{path}({instance_id}): {exc}")
                    if not is_instance_not_found(exc):
                        continue
        raise CtyunClientError("官方 VNC 远程登录接口暂不可用：" + "；".join(errors[:6]))

    def action(self, resource_type: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        region_id = self._region_for_action(payload)
        resource_id = payload.get("resource_id")
        if resource_type == "ecs":
            instance_id = self._ecs_instance_id_candidates(payload)[0] if self._ecs_instance_id_candidates(payload) else ""
            if action == "create":
                region_id = self._region_for_action(payload)
                body = self._ecs_create_body(payload, region_id)
                if not body.get("instanceName") or not (body.get("flavorID") or body.get("flavorName")) or not body.get("imageID") or not body.get("vpcID"):
                    raise CtyunClientError("创建云主机至少需要 instanceName、flavorID/flavorName、imageID、vpcID。")
                return self._post_action(settings.ecs_endpoint, "/v4/ecs/create-instance", body)
            if not instance_id:
                raise CtyunClientError("云主机操作缺少 instanceID。")
            common = {"regionID": region_id, "instanceID": instance_id}
            if action == "start":
                return self._post_ecs_instance_action("/v4/ecs/start-instance", payload, lambda iid: {"regionID": region_id, "instanceID": iid})
            if action == "stop":
                return self._post_ecs_instance_action("/v4/ecs/stop-instance", payload, lambda iid: {"regionID": region_id, "instanceID": iid, "force": bool(payload.get("force", False))})
            if action == "reboot":
                return self._post_ecs_instance_action("/v4/ecs/reboot-instance", payload, lambda iid: {"regionID": region_id, "instanceID": iid})
            if action == "update":
                body = compact({
                    **common,
                    "displayName": payload.get("displayName"),
                    "instanceName": payload.get("instanceName"),
                    "instanceDescription": payload.get("instanceDescription"),
                })
                if len(body) == len(common):
                    raise CtyunClientError("修改云主机至少需要名称或描述。")
                return self._post_action(settings.ecs_endpoint, "/v4/ecs/update-instance", body)
            if action == "reset_password":
                new_password = payload.get("newPassword")
                if not new_password:
                    raise CtyunClientError("重置密码需要 newPassword。")
                return self._post_action(settings.ecs_endpoint, "/v4/ecs/reset-password", compact({
                    **common,
                    "newPassword": new_password,
                    "userName": payload.get("userName"),
                }))
            if action == "rebuild":
                if not payload.get("password") and not payload.get("keyPairID"):
                    raise CtyunClientError("重装系统需要密码或密钥对。")
                return self._post_action(settings.ecs_endpoint, "/v4/ecs/rebuild-instance", compact({
                    **common,
                    "clientToken": str(uuid.uuid4()),
                    "userName": payload.get("userName"),
                    "password": payload.get("password"),
                    "keyPairID": payload.get("keyPairID"),
                    "imageID": payload.get("imageID"),
                    "instanceName": payload.get("instanceName"),
                    "monitorService": as_bool(payload.get("monitorService"), True),
                    "payImage": as_bool(payload.get("payImage"), False),
                }))
            if action == "resize":
                flavor_id = payload.get("flavorID")
                if not flavor_id:
                    raise CtyunClientError("变更规格需要 flavorID。")
                return self._post_action(settings.ecs_endpoint, "/v4/ecs/update-flavor-spec", {
                    **common,
                    "flavorID": flavor_id,
                    "clientToken": str(uuid.uuid4()),
                    "payVoucherPrice": as_float(payload.get("payVoucherPrice"), 0),
                })
            if action == "deletion_protection":
                return self._post_action(
                    settings.ecs_endpoint,
                    "/v4/ecs/update-deletion-protection",
                    {**common, "deletionProtection": as_bool(payload.get("deletionProtection"))},
                )
            if action == "auto_renew":
                return self._post_action(settings.ecs_endpoint, "/v4/ecs/update-auto-renew-config", compact({
                    "regionID": region_id,
                    "instanceIDList": str(instance_id),
                    "autoRenewStatus": as_int(payload.get("autoRenewStatus"), 0),
                    "autoRenewCycleType": payload.get("autoRenewCycleType"),
                    "autoRenewCycleCount": as_int(payload.get("autoRenewCycleCount"), 0),
                }))
            if action in {"change_private_ip", "change_vpc"}:
                nic_ids = self._network_interface_id_candidates(payload)
                if not nic_ids:
                    nic_ids = self._live_network_interface_id_candidates(region_id, instance_id, payload)
                if not nic_ids:
                    raise CtyunClientError("未识别到云主机主网卡 ID，请先同步云主机资源，或在弹窗里手动填写网卡ID。")
                subnet_id = payload.get("subnetID") or payload.get("subnetId")
                private_ip = payload.get("privateIP") or payload.get("privateIp") or payload.get("ipAddress")
                vpc_id = payload.get("vpcID") or payload.get("vpcId")
                security_group_id = payload.get("securityGroupID") or payload.get("securityGroupId") or payload.get("secGroupList")
                security_group_ids = split_csv(payload.get("securityGroupIDList") or payload.get("securityGroupIDs") or security_group_id)
                if not subnet_id:
                    raise CtyunClientError("修改网络需要选择目标子网。")
                if action == "change_vpc" and not vpc_id:
                    raise CtyunClientError("更换 VPC 需要选择目标 VPC。")
                errors: list[str] = []
                for nic_id in nic_ids:
                    if action == "change_private_ip":
                        variants = [
                            compact({"regionID": region_id, "networkInterfaceID": nic_id, "subnetID": subnet_id, "privateIP": private_ip, "clientToken": str(uuid.uuid4())}),
                            compact({"regionID": region_id, "portID": nic_id, "subnetID": subnet_id, "privateIP": private_ip, "clientToken": str(uuid.uuid4())}),
                            compact({"regionID": region_id, "networkInterfaceID": nic_id, "subnetID": subnet_id, "ipAddress": private_ip, "clientToken": str(uuid.uuid4())}),
                            compact({"regionID": region_id, "portID": nic_id, "subnetID": subnet_id, "ipAddress": private_ip, "clientToken": str(uuid.uuid4())}),
                        ]
                        path_options = ["/v4/ports/change-private-ip", "/v4/ecs/ports/change-private-ip"]
                    else:
                        variants = [
                            compact({"regionID": region_id, "networkInterfaceID": nic_id, "vpcID": vpc_id, "subnetID": subnet_id, "securityGroupIDList": security_group_ids, "privateIP": private_ip, "clientToken": str(uuid.uuid4())}),
                            compact({"regionID": region_id, "portID": nic_id, "vpcID": vpc_id, "subnetID": subnet_id, "securityGroupIDList": security_group_ids, "privateIP": private_ip, "clientToken": str(uuid.uuid4())}),
                            compact({"regionID": region_id, "networkInterfaceID": nic_id, "vpcID": vpc_id, "subnetID": subnet_id, "securityGroupIDs": security_group_ids, "ipAddress": private_ip, "clientToken": str(uuid.uuid4())}),
                            compact({"regionID": region_id, "portID": nic_id, "vpcID": vpc_id, "subnetID": subnet_id, "securityGroupIDs": security_group_ids, "ipAddress": private_ip, "clientToken": str(uuid.uuid4())}),
                            compact({"regionID": region_id, "networkInterfaceID": nic_id, "vpcID": vpc_id, "subnetID": subnet_id, "securityGroupID": security_group_ids[0] if security_group_ids else None, "privateIP": private_ip, "clientToken": str(uuid.uuid4())}),
                        ]
                        path_options = ["/v4/ports/change-vpc", "/v4/ecs/ports/change-vpc"]
                    for endpoint in [settings.vpc_endpoint, settings.ecs_endpoint]:
                        for path in path_options:
                            for body in variants:
                                try:
                                    return self._post_action(endpoint, path, body)
                                except CtyunClientError as exc:
                                    errors.append(f"{path}({nic_id}): {exc}")
                                    if not is_instance_not_found(exc):
                                        continue
                raise CtyunClientError(("修改内网IP" if action == "change_private_ip" else "更换VPC") + "接口暂不可用：" + "；".join(errors[:6]))
            if action == "create_image":
                image_name = payload.get("imageName")
                if not image_name:
                    raise CtyunClientError("制作私有镜像需要 imageName。")
                return self._post_action(settings.ims_endpoint, "/v4/image/create", compact({
                    "regionID": region_id,
                    "instanceID": instance_id,
                    "imageName": image_name,
                    "description": payload.get("description"),
                    "enableImageIntegrityCheck": as_bool(payload.get("enableImageIntegrityCheck"), False),
                    "maximumRAM": as_int(payload.get("maximumRAM"), 0),
                    "minimumRAM": as_int(payload.get("minimumRAM"), 0),
                    "projectID": payload.get("projectID") or "0",
                    "labels": [],
                }))
            if action == "release":
                return self._post_ecs_instance_action(
                    "/v4/ecs/destroy-instance",
                    payload,
                    lambda iid: {"regionID": region_id, "instanceID": iid, "clientToken": str(uuid.uuid4())},
                )
            if action == "unsubscribe":
                return self._post_ecs_instance_action(
                    "/v4/ecs/unsubscribe-instance",
                    payload,
                    lambda iid: {
                        "regionID": region_id,
                        "instanceID": iid,
                        "clientToken": str(uuid.uuid4()),
                        "deleteVolume": bool(payload.get("deleteVolume", False)),
                        "deleteEIP": bool(payload.get("deleteEIP", False)),
                    },
                )

        if resource_type == "eip":
            eip_id = payload.get("eipID") or resource_id
            if action == "create":
                body = compact({
                    "clientToken": str(uuid.uuid4()),
                    "regionID": region_id,
                    "name": payload.get("name") or payload.get("eipName"),
                    "bandwidth": as_int(payload.get("bandwidth"), 5),
                    "cycleType": payload.get("cycleType") or "on_demand",
                    "cycleCount": as_int(payload.get("cycleCount")) if payload.get("cycleCount") not in (None, "") else None,
                    "bandwidthID": payload.get("bandwidthID"),
                    "demandBillingType": payload.get("demandBillingType") or "bandwidth",
                    "lineType": payload.get("lineType") or "163",
                    "payVoucherPrice": payload.get("payVoucherPrice"),
                    "projectID": payload.get("projectID") or "0",
                })
                if not body.get("name"):
                    raise CtyunClientError("创建弹性IP需要名称。")
                return self._post_action(settings.eip_endpoint, "/v4/eip/create", body)
            if not eip_id:
                raise CtyunClientError("弹性IP操作缺少 eipID。")
            common = {
                "regionID": region_id,
                "eipID": eip_id,
                "clientToken": str(uuid.uuid4()),
            }
            if action == "bind":
                association_id = payload.get("associationID")
                if not association_id:
                    raise CtyunClientError("绑定弹性IP需要 associationID。")
                return self._post_action(
                    settings.eip_endpoint,
                    "/v4/eip/associate",
                    {
                        **common,
                        "associationID": association_id,
                        "associationType": int(payload.get("associationType", 1)),
                        "projectID": payload.get("projectID", "0"),
                    },
                )
            if action == "unbind":
                return self._post_action(
                    settings.eip_endpoint,
                    "/v4/eip/disassociate",
                    {**common, "projectID": payload.get("projectID", "0")},
                )
            if action == "rename":
                name = payload.get("name")
                if not name:
                    raise CtyunClientError("修改弹性IP名称需要 name。")
                return self._post_action(
                    settings.eip_endpoint,
                    "/v4/eip/change-name",
                    {**common, "name": name, "projectID": payload.get("projectID", "0")},
                )
            if action in {"release", "unsubscribe"}:
                return self._post_action(settings.eip_endpoint, "/v4/eip/delete", common)

        if resource_type == "vpc":
            if action == "create":
                body = compact({
                    "regionID": region_id,
                    "clientToken": str(uuid.uuid4()),
                    "name": payload.get("name"),
                    "CIDR": payload.get("CIDR") or payload.get("cidr"),
                    "description": payload.get("description"),
                    "enableIpv6": as_bool(payload.get("enableIpv6"), False),
                    "projectID": payload.get("projectID") or "0",
                })
                if not body.get("name") or not body.get("CIDR"):
                    raise CtyunClientError("创建 VPC 需要 name 和 CIDR。")
                return self._post_action(settings.vpc_endpoint, "/v4/vpc/create", body)
            if action == "create_subnet":
                body = compact({
                    "regionID": region_id,
                    "clientToken": str(uuid.uuid4()),
                    "name": payload.get("name"),
                    "vpcID": payload.get("vpcID") or payload.get("networkID"),
                    "CIDR": payload.get("CIDR") or payload.get("cidr"),
                    "description": payload.get("description"),
                    "enableIpv6": as_bool(payload.get("enableIpv6"), False),
                    "dnsList": split_csv(payload.get("dnsList")),
                    "subnetGatewayIP": payload.get("subnetGatewayIP") or payload.get("gatewayIP"),
                    "subnetType": payload.get("subnetType") or "common",
                    "projectID": payload.get("projectID") or "0",
                })
                if not body.get("name") or not body.get("vpcID") or not body.get("CIDR"):
                    raise CtyunClientError("创建子网需要 name、vpcID 和 CIDR。")
                return self._post_action(settings.vpc_endpoint, "/v4/vpc/create-subnet", body)
            if action == "update":
                vpc_id = payload.get("vpcID") or resource_id
                if not vpc_id:
                    raise CtyunClientError("修改 VPC 需要 vpcID。")
                return self._post_action(settings.vpc_endpoint, "/v4/vpc/update", compact({
                    "regionID": region_id,
                    "clientToken": str(uuid.uuid4()),
                    "vpcID": vpc_id,
                    "name": payload.get("name"),
                    "description": payload.get("description"),
                    "dnsHostnamesEnabled": as_int(payload.get("dnsHostnamesEnabled"), 0),
                    "projectID": payload.get("projectID") or "0",
                }))
            if action == "delete":
                vpc_id = payload.get("vpcID") or resource_id
                if not vpc_id:
                    raise CtyunClientError("删除 VPC 需要 vpcID。")
                return self._post_action(settings.vpc_endpoint, "/v4/vpc/delete", {"regionID": region_id, "clientToken": str(uuid.uuid4()), "vpcID": vpc_id})

        if resource_type == "subnet":
            subnet_id = payload.get("subnetID") or resource_id
            if not subnet_id:
                raise CtyunClientError("子网操作需要 subnetID。")
            if action == "update":
                return self._post_action(settings.subnet_endpoint, "/v4/vpc/update-subnet", compact({
                    "regionID": region_id,
                    "subnetID": subnet_id,
                    "name": payload.get("name"),
                    "description": payload.get("description"),
                    "dnsList": split_csv(payload.get("dnsList")),
                }))
            if action == "delete":
                return self._post_action(
                    settings.subnet_endpoint,
                    "/v4/vpc/delete-subnet",
                    {"regionID": region_id, "clientToken": str(uuid.uuid4()), "subnetID": subnet_id},
                )

        if resource_type == "security_group":
            security_group_id = payload.get("securityGroupID") or resource_id
            common = {
                "regionID": region_id,
                "clientToken": str(uuid.uuid4()),
                "projectID": payload.get("projectID") or "0",
            }
            if action == "create":
                if not payload.get("vpcID") or not payload.get("name"):
                    raise CtyunClientError("创建安全组需要 vpcID 和 name。")
                return self._post_action(settings.vpc_endpoint, "/v4/vpc/create-security-group", {
                    **common,
                    "vpcID": payload.get("vpcID"),
                    "name": payload.get("name"),
                    "description": payload.get("description") or "",
                })
            if not security_group_id:
                raise CtyunClientError("安全组操作需要 securityGroupID。")
            if action == "update":
                return self._post_action(settings.vpc_endpoint, "/v4/vpc/modify-security-group-attribute", compact({
                    **common,
                    "securityGroupID": security_group_id,
                    "name": payload.get("name"),
                    "description": payload.get("description"),
                    "enabled": as_bool(payload.get("enabled"), True),
                }))
            if action == "create_rule":
                direction = payload.get("direction") or "ingress"
                if direction not in {"ingress", "egress"}:
                    raise CtyunClientError("安全组规则方向无效。")
                rule = compact({
                    "direction": direction,
                    "action": payload.get("ruleAction") or "accept",
                    "protocol": payload.get("protocol") or "ANY",
                    "ethertype": payload.get("ethertype") or "IPv4",
                    "destCidrIp": payload.get("destCidrIp") or ("0.0.0.0/0" if payload.get("ethertype") != "IPv6" else "::/0"),
                    "remoteType": 0,
                    "priority": as_int(payload.get("priority"), 100),
                    "description": payload.get("description"),
                    "range": payload.get("range"),
                })
                return self._post_action(
                    settings.vpc_endpoint,
                    f"/v4/vpc/create-security-group-{direction}",
                    {
                        "regionID": region_id,
                        "securityGroupID": security_group_id,
                        "securityGroupRules": [rule],
                        "clientToken": str(uuid.uuid4()),
                    },
                )
            if action == "delete_rule":
                direction = payload.get("direction") or "ingress"
                rule_id = payload.get("securityGroupRuleID")
                if direction not in {"ingress", "egress"} or not rule_id:
                    raise CtyunClientError("删除安全组规则需要规则方向和规则。")
                return self._post_action(
                    settings.vpc_endpoint,
                    f"/v4/vpc/revoke-security-group-{direction}",
                    {
                        "regionID": region_id,
                        "securityGroupID": security_group_id,
                        "securityGroupRuleID": rule_id,
                        "clientToken": str(uuid.uuid4()),
                    },
                )
            if action == "delete":
                return self._post_action(
                    settings.vpc_endpoint,
                    "/v4/vpc/delete-security-group",
                    {**common, "securityGroupID": security_group_id},
                )

        if resource_type == "route_table":
            route_table_id = payload.get("routeTableID") or resource_id
            common = {"regionID": region_id, "clientToken": str(uuid.uuid4())}
            if action == "create":
                if not payload.get("vpcID") or not payload.get("name"):
                    raise CtyunClientError("创建路由表需要 vpcID 和 name。")
                return self._post_action(settings.vpc_endpoint, "/v4/vpc/route-table/create", compact({
                    **common,
                    "vpcID": payload.get("vpcID"),
                    "name": payload.get("name"),
                    "description": payload.get("description"),
                    "projectID": payload.get("projectID") or "0",
                    "subnetLocalRouteEnabled": as_int(payload.get("subnetLocalRouteEnabled"), 0),
                }))
            if not route_table_id:
                raise CtyunClientError("路由表操作需要 routeTableID。")
            if action == "update":
                return self._post_action(settings.vpc_endpoint, "/v4/vpc/route-table/modify", compact({
                    **common,
                    "routeTableID": route_table_id,
                    "name": payload.get("name"),
                    "description": payload.get("description"),
                    "subnetLocalRouteEnabled": as_int(payload.get("subnetLocalRouteEnabled"), 0),
                }))
            if action == "delete":
                return self._post_action(
                    settings.vpc_endpoint,
                    "/v4/vpc/route-table/delete",
                    {**common, "routeTableID": route_table_id},
                )

        if resource_type == "acl":
            acl_id = payload.get("aclID") or resource_id
            common = {"regionID": region_id, "clientToken": str(uuid.uuid4())}
            if action == "create":
                if not payload.get("vpcID") or not payload.get("name"):
                    raise CtyunClientError("创建 ACL 需要 vpcID 和 name。")
                return self._post_action(settings.vpc_endpoint, "/v4/acl/create", compact({
                    **common,
                    "vpcID": payload.get("vpcID"),
                    "name": payload.get("name"),
                    "description": payload.get("description"),
                    "projectID": payload.get("projectID") or "0",
                    "applyToPublicLb": as_bool(payload.get("applyToPublicLb"), False),
                }))
            if not acl_id:
                raise CtyunClientError("ACL 操作需要 aclID。")
            if action == "update":
                return self._post_action(settings.vpc_endpoint, "/v4/acl/update", compact({
                    "regionID": region_id,
                    "aclID": acl_id,
                    "name": payload.get("name"),
                    "description": payload.get("description"),
                    "enabled": payload.get("enabled"),
                    "projectID": payload.get("projectID") or "0",
                }))
            if action == "delete":
                return self._post_action(
                    settings.vpc_endpoint,
                    "/v4/acl/delete",
                    {**common, "aclID": acl_id},
                )

        if resource_type == "image":
            image_id = payload.get("imageID") or payload.get("resource_id")
            if action == "share":
                destination = payload.get("destinationAccountID") or payload.get("destinationUser")
                if not image_id or not destination:
                    raise CtyunClientError("共享镜像需要 imageID 和接收方天翼云账号ID destinationAccountID。")
                return self._post_action(
                    settings.ims_endpoint,
                    "/v4/image/shared-image/create",
                    {"regionID": region_id, "imageID": image_id, "destinationAccountID": destination},
                )
            if action == "accept":
                return self._post_action(settings.ims_endpoint, "/v4/image/shared-image/accept", {"regionID": region_id, "imageID": image_id})
            if action == "reject":
                return self._post_action(settings.ims_endpoint, "/v4/image/shared-image/reject", {"regionID": region_id, "imageID": image_id})
            if action == "unshare":
                destination = payload.get("destinationAccountID") or payload.get("destinationUser")
                if not destination:
                    raise CtyunClientError("取消共享需要接收方天翼云账号ID destinationAccountID。")
                return self._post_action(
                    settings.ims_endpoint,
                    "/v4/image/shared-image/delete",
                    {"regionID": region_id, "imageID": image_id, "destinationAccountID": destination},
                )
            if action == "copy":
                name = payload.get("imageName")
                if not image_id or not name:
                    raise CtyunClientError("复制镜像需要 imageID 和目标 imageName。")
                return self._post_action(settings.ims_endpoint, "/v4/image/copy", compact({
                    "regionID": region_id,
                    "imageID": image_id,
                    "imageName": name,
                    "description": payload.get("description"),
                    "projectID": payload.get("projectID") or "0",
                    "labels": [],
                }))
            if action == "delete":
                if not image_id:
                    raise CtyunClientError("删除镜像需要 imageID。")
                return self._post_action(settings.ims_endpoint, "/v4/image/delete", {"regionID": region_id, "imageID": image_id})

        if resource_type == "vip":
            vip_id = payload.get("haVipID") or payload.get("resource_id")
            if action == "create":
                subnet_id = payload.get("subnetID")
                if not subnet_id:
                    raise CtyunClientError("创建虚拟IP需要 subnetID。")
                body = {
                    "regionID": region_id,
                    "clientToken": str(uuid.uuid4()),
                    "subnetID": subnet_id,
                    "networkID": payload.get("networkID") or payload.get("vpcID"),
                    "ipAddress": payload.get("ipAddress") or None,
                    "vipType": payload.get("vipType") or "v4",
                }
                return self._post_action(settings.vip_endpoint, "/v4/vpc/havip/create", body)
            if action in {"bind_ecs", "bind_eip", "unbind_ecs", "unbind_eip"}:
                if not vip_id:
                    raise CtyunClientError("绑定/解绑虚拟IP需要 haVipID。")
                is_bind = action.startswith("bind")
                is_eip = action.endswith("eip")
                body = {
                    "regionID": region_id,
                    "clientToken": str(uuid.uuid4()),
                    "haVipID": vip_id,
                    "resourceType": "NETWORK" if is_eip else "VM",
                }
                if is_eip:
                    body["floatingID"] = payload.get("floatingID") or payload.get("eipID")
                    if not body["floatingID"]:
                        raise CtyunClientError("绑定/解绑弹性IP需要 floatingID/eipID。")
                else:
                    body["instanceID"] = payload.get("instanceID")
                    body["networkInterfaceID"] = payload.get("networkInterfaceID")
                    if not body["instanceID"] or not body["networkInterfaceID"]:
                        raise CtyunClientError("绑定/解绑云主机需要 instanceID 和 networkInterfaceID。")
                return self._post_action(settings.vip_endpoint, "/v4/vpc/havip/bind" if is_bind else "/v4/vpc/havip/unbind", body)
            if action == "delete":
                if not vip_id:
                    raise CtyunClientError("删除虚拟IP需要 haVipID。")
                return self._post_action(settings.vip_endpoint, "/v4/vpc/havip/delete", {"regionID": region_id, "clientToken": str(uuid.uuid4()), "haVipID": vip_id})

        raise CtyunClientError(f"{resource_type}.{action} 的正式接口尚未配置。")

    def get_balance(self) -> dict[str, Any]:
        raise CtyunClientError("余额查询公开 OpenAPI 未配置。若天翼云给你开通费用接口，请补充 endpoint/path 后接入。")


def build_client(account: dict[str, Any], mode: str = "openapi"):
    if mode != "openapi":
        raise CtyunClientError("正式版已关闭模拟数据，请将 CTYUN_MANAGER_CTYUN_MODE 设置为 openapi。")
    return CtyunOpenApiClient(account)


def build_region_client(account: dict[str, Any], mode: str = "openapi"):
    if mode != "openapi":
        raise CtyunClientError("正式版已关闭模拟数据，请将 CTYUN_MANAGER_CTYUN_MODE 设置为 openapi。")
    return CtyunOpenApiClient(account, require_region=False)
