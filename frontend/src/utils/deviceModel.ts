// 小米音箱 hardware -> 型号名称 / 型号图片 映射
// 图片位于 public/devicespic/*.webp, 通过 /devicespic/<image> 访问
//
// 型号名称与 hardware 以 miot-spec 设备库 (xiaomi.wifispeaker) 为准。
// 个别实机上报的别名与官方码并存 (LX05A/LX5A、L07A/L7A、L16B/16B), 两者都保留。
//
// 注意: getDeviceModelInfo 按子串匹配且按插入顺序命中, 互为子串的码必须长的在前
// (OH27 / OH2P 在 OH2 前, LX05A 在 LX05 前, 16B 在 L16B 前), 否则会命中错误型号。

export interface DeviceModelInfo {
  model: string
  image: string
}

const FALLBACK_IMAGE = 'L05B.webp'
// 带屏型号家族共用的示意图 (没有单独图片的带屏型号沿用家族图)
const SCREEN_IMAGE_LEGACY = 'LX04_X10A_X08A.webp'
const SCREEN_IMAGE = 'X08C_X08E_X8F.webp'

const DEVICE_MODEL_MAP: Record<string, DeviceModelInfo> = {
  // ---- 带屏型号: 触屏音箱 / 智能家庭屏 ----
  LX04: { model: '小爱触屏音箱', image: SCREEN_IMAGE_LEGACY },
  L04M: { model: '小爱触屏音箱 (2021款)', image: SCREEN_IMAGE_LEGACY },
  X08A: { model: '小爱触屏音箱 Pro 8', image: SCREEN_IMAGE_LEGACY },
  X10A: { model: 'Xiaomi 智能家庭屏 10', image: SCREEN_IMAGE_LEGACY },
  X08C: { model: 'Redmi小爱触屏音箱 8', image: SCREEN_IMAGE },
  X08E: { model: 'Redmi小爱触屏音箱 Pro 8英寸', image: SCREEN_IMAGE },
  X8F: { model: 'Xiaomi 智能家庭屏 Pro 8', image: SCREEN_IMAGE },
  X8S: { model: 'Xiaomi 触屏音箱 8', image: SCREEN_IMAGE },
  ASX4B: { model: 'Xiaomi 智能家庭屏 Mini', image: SCREEN_IMAGE }, // X4B 历史别名
  X4B: { model: 'Xiaomi 智能家庭屏 Mini', image: SCREEN_IMAGE },
  X6A: { model: 'Xiaomi 智能家庭屏 6', image: SCREEN_IMAGE },
  OH11: { model: 'Xiaomi 智能家庭屏 11', image: SCREEN_IMAGE },
  OH27: { model: 'Xiaomi 智能家庭屏 Max 27', image: SCREEN_IMAGE },
  OH8: { model: 'Xiaomi 智能家庭屏 8 云台版', image: SCREEN_IMAGE },

  // ---- 非触屏型号: 小爱音箱系列 ----
  S12: { model: '小米AI音箱', image: 'S12_S12A_MDZ-25-DA.webp' },
  S12A: { model: '小米AI音箱', image: 'S12_S12A_MDZ-25-DA.webp' },
  'MDZ-25-DA': { model: '小米AI音箱', image: 'S12_S12A_MDZ-25-DA.webp' },
  L15A: { model: '小米AI音箱（第二代）', image: 'L15A.webp' },
  L06A: { model: '小爱音箱', image: 'L06A.webp' },
  LX01: { model: '小爱音箱mini', image: 'LX01.webp' },
  LX06: { model: '小爱音箱Pro', image: 'LX06.webp' },
  LX05A: { model: '小爱音箱 万能遥控版', image: 'LX5A.webp' }, // 历史写法 (须在 LX05 前)
  LX5A: { model: '小爱音箱 万能遥控版', image: 'LX5A.webp' },
  LX05: { model: '小爱音箱Play (2019款)', image: 'LX05.webp' },
  L05B: { model: '小爱音箱Play', image: 'L05B.webp' },
  L05C: { model: '小爱音箱Play 增强版', image: 'L05C.webp' },
  L05G: { model: 'Xiaomi Smart Speaker (IR Control)', image: FALLBACK_IMAGE },
  L09A: { model: '小爱音箱Art', image: 'L09A.webp' },
  L09B: { model: '小爱音箱 Art 电池版', image: 'L09A.webp' },
  L07A: { model: 'Redmi小爱音箱 Play', image: 'L07A.webp' }, // 历史写法
  L7A: { model: 'Redmi小爱音箱 Play', image: 'L07A.webp' },

  // ---- 非触屏型号: Xiaomi Sound / 智能音箱系列 ----
  '16B': { model: 'Xiaomi Sound', image: 'L16A.webp' }, // 官方码 (须在 L16B 前)
  L16B: { model: 'Xiaomi Sound', image: 'L16A.webp' }, // #1 实机上报码
  L16A: { model: 'Xiaomi Sound', image: 'L16A.webp' },
  L17A: { model: 'Xiaomi Sound Pro', image: 'L17A.webp' },
  '07G': { model: 'Xiaomi Smart Speaker Lite', image: FALLBACK_IMAGE },
  M03A: { model: 'Xiaomi Sound Move', image: FALLBACK_IMAGE },
  OH1M: { model: 'Xiaomi Sound 2 Max', image: FALLBACK_IMAGE },
  OH1P: { model: 'Xiaomi Sound 2 Pro', image: FALLBACK_IMAGE },
  OH2P: { model: 'Xiaomi 智能音箱 Pro', image: 'OH2P.webp' }, // 须在 OH2 前
  OH2: { model: 'Xiaomi 智能音箱', image: 'OH2.webp' },

  // ---- 其它 ----
  M01: { model: '小爱音箱HD', image: 'L05B.webp' },
  XMYX01JY: { model: '小爱音箱HD', image: 'L05B.webp' },
}

/** 根据 hardware 匹配型号信息, 未命中时返回原始 hardware 作为型号名 + 兜底图片 */
export function getDeviceModelInfo(hardware: string): DeviceModelInfo {
  const h = hardware || ''
  for (const key of Object.keys(DEVICE_MODEL_MAP)) {
    if (h.includes(key)) return DEVICE_MODEL_MAP[key]
  }
  return { model: h || '未知音箱', image: FALLBACK_IMAGE }
}

/** 型号图片的可访问 URL */
export function getDeviceImageUrl(hardware: string): string {
  return `/devicespic/${getDeviceModelInfo(hardware).image}`
}
