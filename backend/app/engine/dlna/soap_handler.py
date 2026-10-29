"""SOAP 动作解析与分发"""

import logging
import xml.etree.ElementTree as ET

from app.engine.const import (
    AVTRANSPORT_URN,
    CONNECTION_MANAGER_URN,
    RENDERING_CONTROL_URN,
    SUPPORTED_PROTOCOLS,
    UPNP_ERROR_ACTION_FAILED,
    UPNP_ERROR_INVALID_ACTION,
    UPNP_ERROR_INVALID_ARGS,
    UPNP_ERROR_SEEK_MODE_NOT_SUPPORTED,
)
from app.engine.dlna.renderer import DLNARenderer
from app.engine.dlna.templates import soap_fault, soap_response
from app.engine.dlna.xmlsafe import safe_fromstring

log = logging.getLogger("miair")

# SOAP 命名空间
SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"

# 与 SCPD 对外声明保持一致 (templates.RENDERING_CONTROL_SCPD 的 Volume
# allowedValueRange 为 0-100, step 1), 越界值按 UPnP 规范回 402 而非静默截断
VOLUME_MIN = 0
VOLUME_MAX = 100

# SCPD 中 A_ARG_TYPE_SeekMode 的 allowedValueList
SEEK_MODES = ("REL_TIME", "TRACK_NR")


class InvalidArgs(ValueError):
    """SOAP 参数缺失或格式非法 (对应 UPnP 402 Invalid Args)"""


def _require_int(
    params: dict, key: str, *, minimum: int, maximum: int
) -> int:
    """取整型参数, 缺失/非数字/越界时抛 InvalidArgs

    DLNA 控制端点无鉴权且局域网可达, 参数完全由控制点决定。原实现直接
    int(params.get(...)), 一个 DesiredVolume="abc" 就会抛未捕获 ValueError,
    经 aiohttp 变成 500 + 堆栈日志, 既不符合 UPnP 规范也污染日志。

    范围由调用方按 SCPD 声明传入 (当前只有 Volume: 0-100), 不做隐式截断。
    """
    raw = params.get(key)
    if raw is None or raw == "":
        raise InvalidArgs(f"缺少必填参数 {key}")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise InvalidArgs(f"参数 {key} 不是合法整数: {raw[:32]!r}") from None
    if not minimum <= value <= maximum:
        raise InvalidArgs(f"参数 {key} 超出允许范围 {minimum}-{maximum}: {value}")
    return value


def _require_bool(params: dict, key: str, *, default: bool = False) -> bool:
    """取 boole 型参数, 接受 UPnP 惯用的 0/1 与 true/false/yes/no"""
    raw = params.get(key)
    if raw is None or raw == "":
        return default
    value = str(raw).strip().lower()
    if value in ("1", "true", "yes"):
        return True
    if value in ("0", "false", "no"):
        return False
    raise InvalidArgs(f"参数 {key} 不是合法布尔值: {raw[:32]!r}")


def _require_time(params: dict, key: str) -> str:
    """校验 HH:MM:SS / MM:SS 形式的时间串, 格式非法时抛 InvalidArgs

    renderer._parse_time 对无法解析的输入静默返回 0.0, 于是
    Target="garbage" 会变成"Seek 到 0 秒"并回 200 —— 控制端以为跳转成功,
    实际只是位置被重置。故在入口处拦下格式错误。
    """
    raw = str(params.get(key, "") or "").strip()
    if not raw:
        raise InvalidArgs(f"缺少必填参数 {key}")
    parts = raw.split(":")
    if len(parts) not in (2, 3):
        raise InvalidArgs(f"参数 {key} 不是合法时间格式 (HH:MM:SS): {raw[:32]!r}")
    try:
        values = [float(p) for p in parts]
    except ValueError:
        raise InvalidArgs(
            f"参数 {key} 不是合法时间格式 (HH:MM:SS): {raw[:32]!r}"
        ) from None
    if any(v < 0 for v in values):
        raise InvalidArgs(f"参数 {key} 含负数值: {raw[:32]!r}")
    # 负号必须显式拦住: float("-00") 等于 -0.0, 不满足 v < 0, 会绕过上面的检查
    if "-" in raw or "+" in raw:
        raise InvalidArgs(f"参数 {key} 不得包含符号字符: {raw[:32]!r}")
    # 时/分必须是整数 (00:1.5:30 无意义)
    if any(not v.is_integer() for v in values[:-1]):
        raise InvalidArgs(f"参数 {key} 的时/分必须为整数: {raw[:32]!r}")
    return raw


def parse_soap_action(soap_action_header: str) -> tuple[str, str]:
    """从 SOAPAction header 解析服务 URN 和动作名

    Args:
        soap_action_header: e.g. '"urn:schemas-upnp-org:service:AVTransport:1#Play"'

    Returns:
        (service_urn, action_name)
    """
    # 移除引号
    header = soap_action_header.strip('"')
    if "#" in header:
        service_urn, action = header.split("#", 1)
        return service_urn, action
    return "", header


def parse_soap_body(body: str) -> dict[str, str]:
    """解析 SOAP Body 中的参数

    Returns:
        dict: 参数名 -> 参数值
    """
    params = {}
    try:
        root = safe_fromstring(body)
        # 找到 Body 下的 action 元素
        body_elem = root.find(f".//{{{SOAP_NS}}}Body")
        if body_elem is None:
            return params

        # action 元素是 Body 的第一个子元素
        action_elem = list(body_elem)[0] if len(body_elem) > 0 else None
        if action_elem is None:
            return params

        # 提取所有参数
        for child in action_elem:
            # 移除命名空间前缀
            tag = child.tag
            if "}" in tag:
                tag = tag.split("}", 1)[1]
            params[tag] = child.text or ""
    except ET.ParseError as e:
        log.warning(f"SOAP XML 解析错误: {e}")
    return params


async def handle_soap_request(
    renderer: DLNARenderer,
    service_urn: str,
    action: str,
    params: dict[str, str],
) -> tuple[str, int]:
    """处理 SOAP 请求并返回响应

    参数校验失败 (InvalidArgs) 统一转成 UPnP 402 Fault, 未预期异常兜底转成
    501 Action Failed —— 该端点无鉴权且局域网可达, 任何畸形请求都不应冒泡成
    HTTP 500 与堆栈日志 (原实现在 int() 转换处即抛出未捕获 ValueError)。

    Returns:
        (response_xml, http_status_code)
    """
    log.debug(f"[{renderer.friendly_name}] SOAP: {action} params={params}")

    try:
        if service_urn == AVTRANSPORT_URN:
            return await _handle_avtransport(renderer, action, params)
        elif service_urn == RENDERING_CONTROL_URN:
            return await _handle_rendering_control(renderer, action, params)
        elif service_urn == CONNECTION_MANAGER_URN:
            return _handle_connection_manager(action, params)
        else:
            return soap_fault(UPNP_ERROR_INVALID_ACTION, "Invalid Service"), 500
    except InvalidArgs as e:
        log.warning(f"[{renderer.friendly_name}] SOAP 参数非法 ({action}): {e}")
        return soap_fault(UPNP_ERROR_INVALID_ARGS, str(e)), 500
    except Exception as e:  # noqa: BLE001
        # 兜底: 协议层任何未预期异常都转成规范 Fault, 不让控制点收到 500 空响应
        log.error(
            f"[{renderer.friendly_name}] SOAP 动作执行失败 ({action}): {e}",
            exc_info=True,
        )
        return soap_fault(UPNP_ERROR_ACTION_FAILED, f"Action failed: {action}"), 500


async def _handle_avtransport(
    renderer: DLNARenderer, action: str, params: dict
) -> tuple[str, int]:
    """处理 AVTransport 动作"""

    if action == "SetAVTransportURI":
        uri = params.get("CurrentURI", "")
        metadata = params.get("CurrentURIMetaData", "")
        success = await renderer.set_av_transport_uri(uri, metadata)
        if success:
            return soap_response(AVTRANSPORT_URN, action, {}), 200
        # UPnP 惯例: Fault 体一律以 HTTP 500 承载, 具体错误码放在 <errorCode> 里。
        # 此处原用 715 (非标准 HTTP 状态码), 控制点按 HTTP 语义解析时会困惑。
        return soap_fault(
            UPNP_ERROR_ACTION_FAILED, "Unsupported media type (video files not supported)"
        ), 500

    elif action == "Play":
        success = await renderer.play()
        if success:
            return soap_response(AVTRANSPORT_URN, action, {}), 200
        return soap_fault(UPNP_ERROR_ACTION_FAILED, "Play failed"), 500

    elif action == "Pause":
        await renderer.pause()
        return soap_response(AVTRANSPORT_URN, action, {}), 200

    elif action == "Stop":
        await renderer.stop()
        return soap_response(AVTRANSPORT_URN, action, {}), 200

    elif action == "Seek":
        unit = params.get("Unit", "REL_TIME")
        if unit not in SEEK_MODES:
            raise InvalidArgs(f"不支持的 SeekMode: {unit[:32]!r} (仅 {SEEK_MODES})")
        target = _require_time(params, "Target")
        success = await renderer.seek(unit, target)
        if success:
            return soap_response(AVTRANSPORT_URN, action, {}), 200
        return soap_fault(UPNP_ERROR_SEEK_MODE_NOT_SUPPORTED, "Seek mode not supported"), 500

    elif action == "Next":
        await renderer.next_track()
        return soap_response(AVTRANSPORT_URN, action, {}), 200

    elif action == "Previous":
        await renderer.previous_track()
        return soap_response(AVTRANSPORT_URN, action, {}), 200

    elif action == "SetNextAVTransportURI":
        uri = params.get("NextURI", "")
        metadata = params.get("NextURIMetaData", "")
        await renderer.set_next_av_transport_uri(uri, metadata)
        return soap_response(AVTRANSPORT_URN, action, {}), 200

    elif action == "GetCurrentTransportActions":
        actions = renderer.get_current_transport_actions()
        return soap_response(AVTRANSPORT_URN, action, {"Actions": actions}), 200

    elif action == "GetTransportInfo":
        info = renderer.get_transport_info()
        return soap_response(AVTRANSPORT_URN, action, info), 200

    elif action == "GetPositionInfo":
        info = renderer.get_position_info()
        return soap_response(AVTRANSPORT_URN, action, info), 200

    elif action == "GetMediaInfo":
        info = renderer.get_media_info()
        return soap_response(AVTRANSPORT_URN, action, info), 200

    elif action == "GetTransportSettings":
        info = renderer.get_transport_settings()
        return soap_response(AVTRANSPORT_URN, action, info), 200

    elif action == "GetDeviceCapabilities":
        return soap_response(AVTRANSPORT_URN, action, {
            "PlayMedia": "NETWORK",
            "RecMedia": "NOT_IMPLEMENTED",
            "RecQualityModes": "NOT_IMPLEMENTED",
        }), 200

    elif action == "SetPlayMode":
        play_mode = str(params.get("NewPlayMode", "")).strip()
        if not play_mode:
            raise InvalidArgs("缺少必填参数 NewPlayMode")
        if not renderer.set_play_mode(play_mode):
            raise InvalidArgs(
                f"不支持的播放模式 {play_mode!r}; 当前支持 NORMAL 和 REPEAT_ONE"
            )
        return soap_response(AVTRANSPORT_URN, action, {}), 200

    else:
        log.warning(f"未实现的 AVTransport 动作: {action}")
        return soap_fault(UPNP_ERROR_INVALID_ACTION, f"Unknown action: {action}"), 500


async def _handle_rendering_control(
    renderer: DLNARenderer, action: str, params: dict
) -> tuple[str, int]:
    """处理 RenderingControl 动作"""

    if action == "GetVolume":
        volume = await renderer.get_volume()
        return soap_response(RENDERING_CONTROL_URN, action, {"CurrentVolume": str(volume)}), 200

    elif action == "SetVolume":
        volume = _require_int(
            params, "DesiredVolume", minimum=VOLUME_MIN, maximum=VOLUME_MAX
        )
        await renderer.set_volume(volume)
        return soap_response(RENDERING_CONTROL_URN, action, {}), 200

    elif action == "GetMute":
        mute = renderer.get_mute()
        return soap_response(RENDERING_CONTROL_URN, action, {"CurrentMute": "1" if mute else "0"}), 200

    elif action == "SetMute":
        mute = _require_bool(params, "DesiredMute")
        await renderer.set_mute(mute)
        return soap_response(RENDERING_CONTROL_URN, action, {}), 200

    elif action == "ListPresets":
        return soap_response(RENDERING_CONTROL_URN, action, {
            "CurrentPresetNameList": "FactoryDefaults",
        }), 200

    elif action == "SelectPreset":
        return soap_response(RENDERING_CONTROL_URN, action, {}), 200

    else:
        log.warning(f"未实现的 RenderingControl 动作: {action}")
        return soap_fault(UPNP_ERROR_INVALID_ACTION, f"Unknown action: {action}"), 500


def _handle_connection_manager(action: str, params: dict) -> tuple[str, int]:
    """处理 ConnectionManager 动作"""

    if action == "GetProtocolInfo":
        return soap_response(
            CONNECTION_MANAGER_URN,
            action,
            {"Source": "", "Sink": SUPPORTED_PROTOCOLS},
        ), 200

    elif action == "GetCurrentConnectionIDs":
        return soap_response(
            CONNECTION_MANAGER_URN, action, {"ConnectionIDs": "0"}
        ), 200

    elif action == "GetCurrentConnectionInfo":
        return soap_response(
            CONNECTION_MANAGER_URN,
            action,
            {
                "RcsID": "0",
                "AVTransportID": "0",
                "ProtocolInfo": "",
                "PeerConnectionManager": "",
                "PeerConnectionID": "-1",
                "Direction": "Input",
                "Status": "OK",
            },
        ), 200

    else:
        return soap_fault(UPNP_ERROR_INVALID_ACTION, f"Unknown action: {action}"), 500
