"""敏感信息脱敏工具 (cookie / 设备信息), 移植自 MiAir web/api.py"""

# passToken 在返回给前端时使用的完整脱敏占位符 (不是真实凭据)
MASKED_TOKEN = "********"


def _mask_value(key: str, value: str) -> str:
    """按字段生成脱敏后的展示值。

    - passToken: 完整敏感凭据, 全部替换为占位符;
    - userId: 仅为账号标识, 保留最后 3 位明文, 其余用 * 覆盖。
    其它字段保持原样。
    """
    if not value:
        return value
    if key == "passToken":
        return MASKED_TOKEN
    if key == "userId":
        if len(value) <= 3:
            return MASKED_TOKEN
        return MASKED_TOKEN + value[-3:]
    return value


def mask_cookie(cookie: str) -> str:
    """对返回给前端的 cookie 进行脱敏, 隐藏 passToken 与 userId 的敏感部分"""
    if not cookie:
        return cookie
    parts = []
    for item in cookie.split(";"):
        stripped = item.strip()
        if not stripped:
            continue
        if "=" in stripped:
            key, value = stripped.split("=", 1)
            key = key.strip()
            parts.append(f"{key}={_mask_value(key, value.strip())}")
            continue
        parts.append(stripped)
    return "; ".join(parts)


def unmask_cookie(new_cookie: str, current_cookie: str) -> str:
    """将前端回写的 cookie 还原为真实值。

    若某字段回写值仍带 `*` (用户未修改), 则用当前已存储的真实值替换;
    用户填入的新值不含 `*`, 按原样保存。
    """
    if not new_cookie or MASKED_TOKEN not in new_cookie:
        return new_cookie

    current = {}
    for item in (current_cookie or "").split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            current[k.strip()] = v.strip()

    parts = []
    for item in new_cookie.split(";"):
        stripped = item.strip()
        if not stripped:
            continue
        if "=" in stripped:
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip()
            if MASKED_TOKEN in value and current.get(key):
                parts.append(f"{key}={current[key]}")
                continue
            parts.append(f"{key}={value}")
            continue
        parts.append(stripped)
    return "; ".join(parts)


def mask_secret(value: str) -> str:
    """通用密钥脱敏 (如 WxPusher SPT): 仅保留最后 3 位明文"""
    if not value:
        return value
    if len(value) <= 3:
        return MASKED_TOKEN
    return MASKED_TOKEN + value[-3:]


def unmask_secret(new_value: str, current_value: str) -> str:
    """前端回写仍带脱敏占位符 (未修改) 时, 保留已存储的真实值"""
    if new_value and MASKED_TOKEN in new_value:
        return current_value
    return new_value


def mask_devices(device_list, required_fields=None):
    """按白名单裁剪设备信息, 仅保留 required_fields 指定的字段 (支持点号嵌套路径)"""
    if required_fields is None:
        required_fields = ["miotDID", "hardware", "name"]

    single = not isinstance(device_list, list)
    devices = [device_list] if single else device_list

    _MISSING = object()
    result = []
    for device in devices:
        masked = {}
        for field in required_fields:
            keys = field.split(".")

            value = device
            for k in keys:
                if isinstance(value, dict) and k in value:
                    value = value[k]
                else:
                    value = _MISSING
                    break
            if value is _MISSING:
                continue

            target = masked
            for k in keys[:-1]:
                nested = target.get(k)
                if not isinstance(nested, dict):
                    nested = {}
                    target[k] = nested
                target = nested
            target[keys[-1]] = value

        result.append(masked)

    return result[0] if single else result
