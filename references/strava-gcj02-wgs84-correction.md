# GCJ-02 → WGS-84 坐标纠偏 + FIT 文件 patch 完整实现

## 背景

[xqdoo00o/strava_auto](https://github.com/xqdoo00o/strava_auto) 的核心功能：GPS 记录为 GCJ-02（中国国测局偏移坐标系）而非 WGS-84，Strava/OpenStreetMap 用 WGS-84 导致路线整体偏移。

本实现完整复刻其算法，并实现 FIT 二进制级别的坐标 patch + CRC 重建 + gzip 上传。

## 算法：GCJ-02 → WGS-84（精确迭代法）

```python
import math

EARTH_R = 6378137.0
EE = 0.00669342162296594323  # 第一偏心率平方 (a²-b²)/a²

def outOfChina(lat, lng):
    """判断坐标是否在中国境外"""
    if lng < 72.004 or lng > 137.8347: return True
    if lat < 0.8293 or lat > 55.8271: return True
    return False

def _transform(x, y):
    """计算 GCJ→WGS 偏移量的辅助函数（多项式展开）"""
    xy = x * y
    absX = math.sqrt(abs(x))
    xPi = x * math.pi
    yPi = y * math.pi
    d = 20.0 * math.sin(6.0 * xPi) + 20.0 * math.sin(2.0 * xPi)
    lat = d
    lng = d
    lat += 20.0 * math.sin(yPi) + 40.0 * math.sin(yPi / 3.0)
    lng += 20.0 * math.sin(xPi) + 40.0 * math.sin(xPi / 3.0)
    lat += 160.0 * math.sin(yPi / 12.0) + 320.0 * math.sin(yPi / 30.0)
    lng += 150.0 * math.sin(xPi / 12.0) + 300.0 * math.sin(xPi / 30.0)
    lat *= 2.0 / 3.0
    lng *= 2.0 / 3.0
    lat += -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * xy + 0.2 * absX
    lng += 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * xy + 0.1 * absX
    return [lat, lng]

def _delta(lat, lng):
    """计算经纬度差值"""
    t = _transform(lng - 105.0, lat - 35.0)
    dLat = t[0]
    dLng = t[1]
    radLat = lat / 180.0 * math.pi
    magic = math.sin(radLat)
    magic = 1 - EE * magic * magic
    sqrtMagic = math.sqrt(magic)
    dLat = (dLat * 180.0) / ((EARTH_R * (1 - EE)) / (magic * sqrtMagic) * math.pi)
    dLng = (dLng * 180.0) / (EARTH_R / sqrtMagic * math.cos(radLat) * math.pi)
    return [dLat, dLng]

def gcj2wgs(gcjLat, gjclng):
    """GCJ-02 → WGS-84（迭代法，阈值 1e-6，最多 30 次）"""
    if outOfChina(gcjLat, gjclng): return [gcjLat, gjclng]
    newLat, newLng = gcjLat, gjclng
    for _ in range(30):
        oldLat, oldLng = newLat, newLng
        d = _delta(newLat, newLng)
        newLat = gcjLat - d[0]
        newLng = gjclng - d[1]
        if max(abs(oldLat - newLat), abs(oldLng - newLng)) < 1e-6: break
    return [newLat, newLng]
```

## FIT 坐标字段换算

```python
def semicircles_to_degrees(s):
    """FIT 半圆坐标 → 十进制度数"""
    return s * (180.0 / 2**31)

def degrees_to_semicircles(d):
    """十进制度数 → FIT 半圆坐标"""
    return int(d * (2**31 / 180.0))
```

## FIT 二进制 patch

### FIT 文件结构

```
14B header | data body (data_size bytes) | 2B CRC-16
```

Header 结构：
- `data[0]` — header_size (=14)
- `data[1]` — protocol version
- `data[2:4]` — profile version (little endian)
- `data[4:8]` — data size (little endian)
- `data[8:12]` — signature ".FIT"
- `data[12:14]` — CRC

### 消息解析

Data message 结构（header bit7 = 0）：
```
1B: header (local msg type | is_definition)
2B: reserved + endian
2B: global message number
1B: num_fields
N × 3B: field definitions (def_num, size, base_type)
Data...
```

Definition message（header bit7 = 1）：直接透传，不修改。

### 坐标字段映射

| 消息类型 | 消息号 | 字段 def_num | 说明 |
|---------|-------|-------------|------|
| Record | 20 | 0=position_lat, 1=position_long | track point |
| Lap | 19 | 0/1=start_lat/lon, 2/3=end_lat/lon | 圈数据 |
| Session | 2 | 0/1=start, 4/5=nec, 6/7=swc | session |
| SegmentLap | 34 | 0/1/2/3 | segment lap |
| CoursePoint | 32 | 0/1 | 导航点 |
| SegmentPoint | 33 | 0/1 | segment point |

### Patch 算法

1. 遍历所有 data message
2. 对于目标消息类型，找到 `(def_num → offset_in_data)` 映射
3. 读取 position_lat (def=0) 和 position_long (def=1) 的 4 字节 sint32
4. `if val == -2147483648` → null，跳过
5. 转换为 degrees → gcj2wgs() → degrees_to_semicircles() → 写回
6. 用配对逻辑（lat→lon 配对）批量替换

## CRC-16 重建

FIT 使用 CRC-16（多项式 0xEDB88320，查询表法）：

```python
def _build_fit_crc_table():
    table = [0] * 256
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ 0xEDB88320 if crc & 1 else crc >> 1
        table[i] = crc
    return table

_FIT_CRC_TABLE = _build_fit_crc_table()

def _fit_crc_update(crc, data):
    for b in data:
        crc = _FIT_CRC_TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc & 0xFFFF
```

CRC 计算范围：**header + body**（不含尾部 CRC 本身）

## gzip 压缩上传

strava_auto 用 gzip 压缩 FIT 后上传：
- Content-Type: `application/gzip`
- data_type: `fit.gz`
- 文件名：`<原名>.fit.gz`

```python
import gzip

with open(fit_path, 'rb') as f:
    raw_bytes = f.read()
compressed = gzip.compress(raw_bytes, compresslevel=6)

files = {"file": (gz_name, compressed, "application/gzip")}
data = {"name": title, "description": desc, "activity_type": at, "data_type": "fit.gz"}
resp = requests.post(UPLOAD_URL, headers=headers, data=data, files=files, timeout=60)
```

## 触发条件

| 情况 | 触发 | 实现 |
|------|------|------|
| Strava 路线偏 50-300m（非漂移） | 判断 outOfChina | 不需要修，是 Strava 地图匹配 |
| FIT 坐标明显 GCJ-02（lon 差 0.1°+）| 用 `--correct-coords` | correct_fit_file() |
| GPS 彻底失锁（坐标飞到 68°N） | 需 Encoder 重建 | 见 fit-rebuild-encoder.md |

## 使用方式

```bash
# 仅解析（不上传）
python3 strava-upload.py --parse-only xxx.fit

# 上传（自动生成标题/描述）
python3 strava-upload.py xxx.fit

# 上传 + 坐标纠偏
python3 strava-upload.py xxx.fit --correct-coords
```

## 与 MAGENE C706 的关系

**⚠️ 重要更正（2026-06-01 推翻旧结论）**：

~~MAGENE C706 固件输出 WGS84，不需要 GCJ-02 转换~~ —— **这是错的**。

实测（05-30 骑行 OSM 双向验证）证明 **MAGENE C706 固件输出 GCJ-02**，路线 vs WGS-84 路网中位偏 72m，方向恒东偏南；转 GCJ-02 路网后骤降到 4.8m。详见 `references/magene-c706-gcj02-offset.md`。

因此 MAGENE C706 用户**应当**使用 `--correct-coords` 参数上传（命令如 `python3 strava-upload.py xxx.fit --correct-coords`），否则 Strava 上路线会偏 300-500m。

本算法适用于所有国产码表（华为 GT/Runner、小米 Watch、佳明中国大陆固件版本等），判断标准统一为 OSM 双向验证：`scripts/detect_gcj02_offset.py` 工具 5-10 分钟出结论。