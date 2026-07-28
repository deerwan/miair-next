// 小米音箱 hardware -> 型号名称 / 型号图片 映射 (移植自 MiAir index.html)
// 图片位于 public/devicespic/*.webp, 通过 /devicespic/<image> 访问

export interface DeviceModelInfo {
  model: string
  image: string
}

const DEVICE_MODEL_MAP: Record<string, DeviceModelInfo> = {
  L06A: { model: '小爱音箱', image: 'L06A.webp' },
  L07A: { model: 'Redmi小爱音箱 Play', image: 'L07A.webp' },
  S12: { model: '小米AI音箱', image: 'S12_S12A_MDZ-25-DA.webp' },
  S12A: { model: '小米AI音箱', image: 'S12_S12A_MDZ-25-DA.webp' },
  'MDZ-25-DA': { model: '小米AI音箱', image: 'S12_S12A_MDZ-25-DA.webp' },
  LX5A: { model: '小爱音箱 万能遥控版', image: 'LX5A.webp' },
  LX05: { model: '小爱音箱Play (2019款)', image: 'LX05.webp' },
  L15A: { model: '小米AI音箱（第二代）', image: 'L15A.webp' },
  L16A: { model: 'Xiaomi Sound', image: 'L16A.webp' },
  L17A: { model: 'Xiaomi Sound Pro', image: 'L17A.webp' },
  LX06: { model: '小爱音箱Pro', image: 'LX06.webp' },
  LX01: { model: '小爱音箱mini', image: 'LX01.webp' },
  L05B: { model: '小爱音箱Play', image: 'L05B.webp' },
  L05C: { model: '小爱音箱Play 增强版', image: 'L05C.webp' },
  L09A: { model: '小爱音箱Art', image: 'L09A.webp' },
  LX04: { model: '小爱触屏音箱', image: 'LX04_X10A_X08A.webp' },
  X10A: { model: '小爱触屏音箱', image: 'LX04_X10A_X08A.webp' },
  X08A: { model: '小爱触屏音箱', image: 'LX04_X10A_X08A.webp' },
  X08C: { model: '小米触屏音箱', image: 'X08C_X08E_X8F.webp' },
  X08E: { model: '小米触屏音箱', image: 'X08C_X08E_X8F.webp' },
  X8F: { model: '小米触屏音箱', image: 'X08C_X08E_X8F.webp' },
  M01: { model: '小爱音箱HD', image: 'L05B.webp' },
  XMYX01JY: { model: '小爱音箱HD', image: 'L05B.webp' },
  OH2P: { model: 'Xiaomi 智能音箱 Pro', image: 'OH2P.webp' },
  OH2: { model: 'Xiaomi 智能音箱', image: 'OH2.webp' },
}

const FALLBACK_IMAGE = 'L05B.webp'

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
