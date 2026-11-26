"""MiAir 常量定义"""

VERSION = "0.11.1"


# SSDP 相关
SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
SSDP_MX = 3
SSDP_ALIVE_INTERVAL = 30  # 秒

# UPnP URN - 声明为音频渲染器（音箱）
DEVICE_TYPE = "urn:schemas-upnp-org:device:MediaRenderer:1"
AVTRANSPORT_URN = "urn:schemas-upnp-org:service:AVTransport:1"
RENDERING_CONTROL_URN = "urn:schemas-upnp-org:service:RenderingControl:1"
CONNECTION_MANAGER_URN = "urn:schemas-upnp-org:service:ConnectionManager:1"

# Transport States
TRANSPORT_STATE_NO_MEDIA = "NO_MEDIA_PRESENT"
TRANSPORT_STATE_STOPPED = "STOPPED"
TRANSPORT_STATE_PLAYING = "PLAYING"
TRANSPORT_STATE_PAUSED = "PAUSED_PLAYBACK"
TRANSPORT_STATE_TRANSITIONING = "TRANSITIONING"

# Transport Status
TRANSPORT_STATUS_OK = "OK"
TRANSPORT_STATUS_ERROR = "ERROR_OCCURRED"

# Play Mode
PLAY_MODE_NORMAL = "NORMAL"
PLAY_MODE_REPEAT_ONE = "REPEAT_ONE"

# UPnP Error Codes
UPNP_ERROR_INVALID_ACTION = 401
UPNP_ERROR_INVALID_ARGS = 402
UPNP_ERROR_ACTION_FAILED = 501
UPNP_ERROR_TRANSITION_NOT_AVAILABLE = 701
UPNP_ERROR_SEEK_MODE_NOT_SUPPORTED = 710

# AirPlay 下发 MP3 转码流的型号: 仅限 ROM 播放器不接受无限 WAV 流的带屏型号 (#40)。
# 注意不能直接套用 NEED_USE_PLAY_MUSIC_API 全集: 该白名单含大量非触屏型号
# (OH2 / LX06 / L16A / L17A ...), 它们能正常播放 WAV, 但收到 .mp3 流地址后
# 不会来拉流 (MiNA 回执 code 0 却无声), 该型号切回 wav 即恢复。
# 型号 → 设备名对照依据 miot-spec 设备库 (xiaomi.wifispeaker), 名称为「触屏音箱 /
# 智能家庭屏」的归入带屏; 新确认的带屏型号需在此追加, 用户也可用 airplay_stream_format 显式指定。
# 末尾 4 款为 2021-2025 年带屏新型号, 按 MIUI Home 同类播放器推断, 尚未实机验证
# (若不拉流, 看门狗会告警, 可显式设为 wav)。
AIRPLAY_MP3_HARDWARE = frozenset({
    "X08C",  # Redmi小爱触屏音箱 8
    "X08E",  # Redmi小爱触屏音箱Pro 8英寸
    "X8F",   # Xiaomi 智能家庭屏 Pro 8
    "X08A",  # 小爱触屏音箱Pro 8
    "X10A",  # Xiaomi 智能家庭屏 10
    "OH11",  # Xiaomi 智能家庭屏 11
    "X6A",   # Xiaomi 智能家庭屏 6
    "X4B",   # Xiaomi智能家庭屏Mini
    "LX04",  # 小爱触屏音箱
    "L04M",  # 小爱触屏音箱 (2021 款硬件)
    "X8S",   # Xiaomi 触屏音箱 8
    "OH27",  # Xiaomi 智能家庭屏 Max 27
    "OH8",   # Xiaomi 智能家庭屏 8 云台版
})

# 需要使用 play_by_music_url 接口的设备型号
# 含带屏型号，使 default_audio_id 指定的封面/歌词能在触屏音箱生效。
# 硬件码以 miot-spec 设备库 (xiaomi.wifispeaker) 为准; 个别实机上报的别名与官方码并存
# (LX05A/LX5A、L07A/L7A、L16B/16B), 不要轻易删掉其中任一个。
NEED_USE_PLAY_MUSIC_API = [
    # 带屏型号 (触屏音箱 / 智能家庭屏)
    "X08C",
    "X08E",
    "X8F",
    "X08A",
    "X10A",
    "OH11",
    "X6A",
    "X4B",
    "LX04",
    "L04M",
    "X8S",
    "OH27",
    "OH8",
    # 非触屏型号 (ROM 经 play_by_url 播放异常, 需走 music api)
    "LX05",
    "LX05A",  # 历史写法
    "LX5A",   # 官方码: 小爱音箱 万能遥控版
    "OH2",
    "OH2P",
    "L05B",
    "L05C",
    "LX06",
    "L06A",
    "L15A",
    "L07A",   # 历史写法
    "L7A",    # 官方码: Redmi小爱音箱 Play
    "L16A",
    "L16B",   # #1 实机上报码 (Xiaomi Sound)
    "16B",    # 官方码: Xiaomi Sound
    "L17A",
]

# 默认 audio_id (用于 play_by_music_url)
DEFAULT_AUDIO_ID = "448161862632079419"

# 支持的协议信息 (ConnectionManager GetProtocolInfo) - 仅音频
SUPPORTED_PROTOCOLS = (
    "http-get:*:audio/mpeg:*,"
    "http-get:*:audio/mp3:*,"
    "http-get:*:audio/mp4:*,"
    "http-get:*:audio/ogg:*,"
    "http-get:*:audio/flac:*,"
    "http-get:*:audio/x-flac:*,"
    "http-get:*:audio/wav:*,"
    "http-get:*:audio/x-wav:*,"
    "http-get:*:audio/aac:*,"
    "http-get:*:audio/x-aac:*,"
    "http-get:*:audio/x-m4a:*,"
    "http-get:*:audio/x-ms-wma:*,"
    "http-get:*:audio/L16:*,"
    "http-get:*:audio/vnd.dlna.adts:*,"
    "http-get:*:audio/ape:*,"
    "http-get:*:audio/*:*"
)

# 小米对话历史 API (用于语音控制)
LATEST_ASK_API = (
    "https://userprofile.mina.mi.com/device_profile/v2/conversation"
    "?source=dialogu&hardware={hardware}&timestamp={timestamp}&limit=2"
)

# 需要通过 Mina 服务获取对话记录的硬件型号
GET_ASK_BY_MINA = {
    "LX04", "L05B", "L05C", "S12", "S12A",
    "LX5A", "L15A", "L16A", "X6A",
}


