#!/usr/bin/env python3
"""
Strava FIT 文件上传脚本 - 智能版
自动从 FIT 解析运动数据，生成智能标题和详细分析描述
用法: python3 strava-upload.py <fit文件路径> [activity_type]
"""

import sys
import os
import json
import requests
import time
import fitdecode.reader
import gzip
import io
from datetime import timezone, timedelta

# ====== 配置区 ======
CLIENT_ID = "217315"
CLIENT_SECRET = "***"  # 从 ~/.hermes/strava_token.json 动态读取
TOKEN_URL = "https://www.strava.com/oauth/token"
UPLOAD_URL = "https://www.strava.com/api/v3/uploads"
TOKEN_FILE = os.path.expanduser("~/.hermes/strava_token.json")

def load_token():
    with open(TOKEN_FILE) as f:
        return json.load(f)

def save_token(data):
    with open(TOKEN_FILE, 'w') as f:
        json.dump(data, f, indent=2)

# ====== FIT 解析 ======

ACTIVITY_TYPES = ["Run", "Ride", "Swim", "Walk", "Hike", "Workout", "WeightTraining", "Yoga"]

# ====== FIT 解析 ======

def semicircles_to_degrees(s):
    """FIT 的半圆坐标转十进制度数"""
    return s * (180.0 / 2**31)

def degrees_to_semicircles(d):
    """十进制度数转 FIT 半圆坐标"""
    return int(d * (2**31 / 180.0))

# ====== GCJ-02 → WGS84 坐标纠偏（精确迭代版） ======
# 算法与 https://github.com/xqdoo00o/strava_auto 完全一致

import math as mathlib

EARTH_R = 6378137.0
EE = 0.00669342162296594323

def outOfChina(lat, lng):
    """判断坐标是否在中国境外"""
    if lng < 72.004 or lng > 137.8347:
        return True
    if lat < 0.8293 or lat > 55.8271:
        return True
    return False

def _transform(x, y):
    """计算 GCJ→WGS 偏移量辅助函数"""
    xy = x * y
    absX = mathlib.sqrt(abs(x))
    xPi = x * mathlib.pi
    yPi = y * mathlib.pi
    d = 20.0 * mathlib.sin(6.0 * xPi) + 20.0 * mathlib.sin(2.0 * xPi)
    lat = d
    lng = d
    lat += 20.0 * mathlib.sin(yPi) + 40.0 * mathlib.sin(yPi / 3.0)
    lng += 20.0 * mathlib.sin(xPi) + 40.0 * mathlib.sin(xPi / 3.0)
    lat += 160.0 * mathlib.sin(yPi / 12.0) + 320.0 * mathlib.sin(yPi / 30.0)
    lng += 150.0 * mathlib.sin(xPi / 12.0) + 300.0 * mathlib.sin(xPi / 30.0)
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
    radLat = lat / 180.0 * mathlib.pi
    magic = mathlib.sin(radLat)
    magic = 1 - EE * magic * magic
    sqrtMagic = mathlib.sqrt(magic)
    dLat = (dLat * 180.0) / ((EARTH_R * (1 - EE)) / (magic * sqrtMagic) * mathlib.pi)
    dLng = (dLng * 180.0) / (EARTH_R / sqrtMagic * mathlib.cos(radLat) * mathlib.pi)
    return [dLat, dLng]

def gcj2wgs(gcjLat, gjclng):
    """GCJ-02 精确转换为 WGS-84（迭代法）"""
    if outOfChina(gcjLat, gjclng):
        return [gcjLat, gjclng]
    newLat = gcjLat
    newLng = gjclng
    oldLat, oldLng = 0, 0
    threshold = 1e-6
    for _ in range(30):
        oldLat = newLat
        oldLng = newLng
        d = _delta(newLat, newLng)
        newLat = gcjLat - d[0]
        newLng = gjclng - d[1]
        if max(abs(oldLat - newLat), abs(oldLng - newLng)) < threshold:
            break
    return [newLat, newLng]


# ====== FIT 文件坐标纠偏 ======
# 读取 FIT，修正所有坐标字段后写回（原地修改，不改其他内容）

def _build_fit_crc_table():
    """构建 FIT CRC 查表"""
    table = [0] * 256
    for i in range(256):
        crc = i
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xEDB88320
            else:
                crc >>= 1
        table[i] = crc
    return table

_FIT_CRC_TABLE = _build_fit_crc_table()

def _fit_crc_update(crc, data):
    """更新 FIT CRC"""
    table = _FIT_CRC_TABLE
    for b in data:
        crc = table[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc & 0xFFFF

# FIT 消息号定义
_FIT_MSG_FILE = 0
_FIT_MSG_FILE_ID = 1
_FIT_MSG_SESSION = 2
FIT_MSG_LAP = 19
FIT_MSG_RECORD = 20
FIT_MSG_COURSE_POINT = 32
FIT_MSG_SEGMENT_POINT = 33
FIT_MSG_SEGMENT_LAP = 34
FIT_MSG_SESSION = 2

# 坐标字段 def_num（来自 FIT profile）
_FIT_FIELD_POSITION_LAT = 0
_FIT_FIELD_POSITION_LONG = 1
_FIT_FIELD_START_POSITION_LAT = 0
_FIT_FIELD_START_POSITION_LONG = 1
_FIT_FIELD_END_POSITION_LAT = 2
_FIT_FIELD_END_POSITION_LONG = 3
_FIT_FIELD_NEC_LAT = 4
_FIT_FIELD_NEC_LONG = 5
_FIT_FIELD_SWC_LAT = 6
_FIT_FIELD_SWC_LONG = 7

def _patch_fit_message(msg_bytes, records_messages):
    """在消息字节中 patch 坐标字段"""
    # FIT 消息结构：
    #   1B : record header (is_definition=0 for data msg)
    #   2B  : local message type + reserved + endian
    #   2B  : global message number
    #   1B  : field count
    #   N*B : field definitions (def_num, size, base_type)
    #   数据

    if len(msg_bytes) < 7:
        return msg_bytes

    header = msg_bytes[0]
    reserved_endian = msg_bytes[1:3]
    msg_num = int.from_bytes(msg_bytes[3:5], 'little')
    num_fields = msg_bytes[5]

    # 只处理我们关心的消息类型
    coord_fields = []
    if msg_num == FIT_MSG_RECORD:
        coord_fields = [(0, _FIT_FIELD_POSITION_LAT), (1, _FIT_FIELD_POSITION_LONG)]
    elif msg_num == FIT_MSG_SEGMENT_LAP:
        coord_fields = [
            (0, _FIT_FIELD_START_POSITION_LAT), (1, _FIT_FIELD_START_POSITION_LONG),
            (2, _FIT_FIELD_END_POSITION_LAT), (3, _FIT_FIELD_END_POSITION_LONG),
        ]
    elif msg_num == FIT_MSG_LAP:
        coord_fields = [
            (0, _FIT_FIELD_START_POSITION_LAT), (1, _FIT_FIELD_START_POSITION_LONG),
            (2, _FIT_FIELD_END_POSITION_LAT), (3, _FIT_FIELD_END_POSITION_LONG),
        ]
    elif msg_num == FIT_MSG_SESSION:
        coord_fields = [
            (0, _FIT_FIELD_START_POSITION_LAT), (1, _FIT_FIELD_START_POSITION_LONG),
            (4, _FIT_FIELD_NEC_LAT), (5, _FIT_FIELD_NEC_LONG),
            (6, _FIT_FIELD_SWC_LAT), (7, _FIT_FIELD_SWC_LONG),
        ]
    elif msg_num == FIT_MSG_COURSE_POINT:
        coord_fields = [(0, _FIT_FIELD_POSITION_LAT), (1, _FIT_FIELD_POSITION_LONG)]
    elif msg_num == FIT_MSG_SEGMENT_POINT:
        # SegmentPoint: position_lat(0), position_long(1)
        coord_fields = [(0, _FIT_FIELD_POSITION_LAT), (1, _FIT_FIELD_POSITION_LONG)]

    if not coord_fields:
        return msg_bytes

    # 解析 field 定义
    field_defs = []
    offset = 6
    for _ in range(num_fields):
        if offset + 3 > len(msg_bytes):
            break
        def_num = msg_bytes[offset]
        size = msg_bytes[offset + 1]
        base_type = msg_bytes[offset + 2]
        field_defs.append((def_num, size, base_type, offset + 3))
        offset += 3

    # 构建 (def_num -> offset_in_data) 映射
    data_offset = offset
    def_num_to_offset = {}
    for def_num, size, base_type, field_offset in field_defs:
        def_num_to_offset[def_num] = (data_offset, size)
        data_offset += size

    # Patch 坐标字段
    result = bytearray(msg_bytes)
    modified = False
    for target_def_num, field_const in coord_fields:
        if target_def_num not in def_num_to_offset:
            continue
        off, size = def_num_to_offset[target_def_num]
        if size not in (4,):
            continue  # 坐标字段应为 4 字节 (sint32)
        val = int.from_bytes(result[off:off + size], 'little', signed=True)
        if val == -2147483648:  # 生效标记 null
            continue
        lat_deg = semicircles_to_degrees(val)
        # 找配对字段
        other_def = None
        if target_def_num in (_FIT_FIELD_POSITION_LAT, _FIT_FIELD_START_POSITION_LAT,
                               _FIT_FIELD_NEC_LAT, _FIT_FIELD_SWC_LAT):
            other_def = target_def_num + 1
        else:
            other_def = target_def_num - 1
        if other_def not in def_num_to_offset:
            continue
        off2, size2 = def_num_to_offset[other_def]
        if size2 != 4:
            continue
        val2 = int.from_bytes(result[off2:off2 + size2], 'little', signed=True)
        if val2 == -2147483648:
            continue
        lon_deg = semicircles_to_degrees(val2)

        wgs = gcj2wgs(lat_deg, lon_deg)
        new_lat_semi = degrees_to_semicircles(wgs[0])
        new_lon_semi = degrees_to_semicircles(wgs[1])

        result[off:off + 4] = new_lat_semi.to_bytes(4, 'little', signed=True)
        result[off2:off2 + 4] = new_lon_semi.to_bytes(4, 'little', signed=True)
        modified = True

    return bytes(result) if not modified else bytes(result)


def correct_fit_file(input_path, output_path=None):
    """读取 FIT 文件，修正所有 GCJ-02 坐标为 WGS-84，写入新文件"""
    if output_path is None:
        output_path = input_path + '.corrected.fit'

    with open(input_path, 'rb') as f:
        data = bytearray(f.read())

    # FIT 文件结构：
    #   14B header
    #   ... data ...
    #   2B CRC

    if len(data) < 14:
        raise ValueError("Invalid FIT file: too short")

    # 解析 header
    header_size = data[0]
    protocol = data[1]
    profile = int.from_bytes(data[2:4], 'little')
    data_size = int.from_bytes(data[4:8], 'little')
    crc = int.from_bytes(data[12:14], 'little')

    if header_size != 14:
        raise ValueError(f"Unsupported FIT header size: {header_size}")

    total_size = 14 + data_size + 2
    if len(data) < total_size:
        raise ValueError(f"Invalid FIT file: expected {total_size}, got {len(data)}")

    body = data[14:14 + data_size]

    # 遍历消息体，每条消息独立处理
    new_body_parts = []
    pos = 0
    while pos < len(body):
        if pos + 7 > len(body):
            break
        header = body[pos]
        reserved_endian = body[pos + 1:pos + 3]
        msg_num = int.from_bytes(body[pos + 3:pos + 5], 'little')
        num_fields = body[pos + 5]

        # definition message (bit7 of header set)
        if header & 0x80:
            # 跳到下一条消息
            field_def_size = 3  # def_num(1) + size(1) + base_type(1)
            msg_size = 6 + num_fields * field_def_size
            new_body_parts.append(body[pos:pos + msg_size])
            pos += msg_size
            continue

        # data message
        # 计算消息大小
        field_def_size = 3
        data_msg_size = 6  # header(1) + reserved/endian(2) + msg_num(2) + num_fields(1)
        for _ in range(num_fields):
            data_msg_size += body[pos + 6 + _ * 3 + 1]  # size 字段

        msg_bytes = body[pos:pos + data_msg_size]
        new_msg = _patch_fit_message(msg_bytes, None)
        new_body_parts.append(new_msg)
        pos += data_msg_size

    new_body = b''.join(new_body_parts)
    new_data_size = len(new_body)
    new_crc = 0

    # CRC 计算：header + body（不含末尾 CRC）
    new_crc = _fit_crc_update(new_crc, data[:14])
    new_crc = _fit_crc_update(new_crc, new_body)
    new_crc &= 0xFFFF

    # 重建文件
    new_data = bytearray(data[:12])
    new_data[4:8] = new_data_size.to_bytes(4, 'little')
    new_data[12:14] = new_crc.to_bytes(2, 'little')
    new_data.extend(new_body)
    new_data.extend(new_crc.to_bytes(2, 'little'))

    with open(output_path, 'wb') as f:
        f.write(new_data)

    corrected_count = 1  # simplified - just flag that we tried
    return output_path


def parse_fit(fit_path: str) -> dict:
    """解析 FIT 文件，提取所有运动数据"""
    data = {
        "records": [],
        "total_distance_m": 0,
        "total_calories": 0,
        "total_elapsed_s": 0,
        "max_speed_mps": 0,
        "avg_speed_mps": 0,
        "max_heart_rate": 0,
        "avg_heart_rate": 0,
        "min_altitude": 99999,
        "max_altitude": 0,
        "elev_gain": 0,      # 总爬升 m
        "elev_loss": 0,      # 总下降 m
        "max_cadence": 0,
        "start_time": None,
        "end_time": None,
        "sport": "Other",
        "sub_sport": "",
        "bike_name": "",
        "product_name": "",
        "manufacturer": "",
        "user_weight_kg": 0,
        "user_height_m": 0,
        "temperature_c": [],
        "wind_speed_mps": 0,
        "wind_direction_deg": 0,
        "humidity": 0,
    }

    speeds = []
    heart_rates = []
    altitudes = []
    cadence_values = []
    last_altitude = None

    with fitdecode.reader.FitReader(fit_path) as fit:
        for record in fit:
            try:
                name = record.name
            except:
                continue

            if not hasattr(record, 'fields'):
                continue

            # Extract field values
            def field_val(field_name):
                for f in record.fields:
                    if f.name == field_name and f.value is not None:
                        return f.value
                return None

            if name == "file_id":
                tc = field_val("time_created")
                if tc:
                    data["start_time"] = tc
                data["product_name"] = field_val("product_name") or ""
                data["manufacturer"] = field_val("manufacturer") or ""

            elif name == "sport":
                sport_map = {
                    "cycling": "Ride",
                    "running": "Run",
                    "swimming": "Swim",
                    "walking": "Walk",
                    "hiking": "Hike",
                }
                s = field_val("sport")
                if s:
                    data["sport"] = sport_map.get(s, s.capitalize() if s else "Other")
                data["sub_sport"] = field_val("name") or ""

            elif name == "bike_profile":
                data["bike_name"] = field_val("name") or ""

            elif name == "user_profile":
                data["user_weight_kg"] = field_val("weight") or 0
                data["user_height_m"] = field_val("height") or 0

            elif name == "session":
                sess_ascent = field_val("total_ascent")
                sess_descent = field_val("total_descent")
                # 仅当 session 有有效数据时使用（优先于 record 逐点累加）
                if sess_ascent and sess_ascent > 0:
                    data["elev_gain"] = sess_ascent
                if sess_descent and sess_descent > 0:
                    data["elev_loss"] = sess_descent

            elif name == "record":
                ts = field_val("timestamp")
                lat = field_val("position_lat")
                lon = field_val("position_long")
                dist = field_val("distance")
                alt = field_val("enhanced_altitude") or field_val("altitude")
                speed = field_val("enhanced_speed") or field_val("speed")
                hr = field_val("heart_rate")
                cad = field_val("cadence")
                cal = field_val("calories")
                temp = field_val("temperature")
                grade = field_val("grade")

                if ts and not data["start_time"]:
                    data["start_time"] = ts
                if ts:
                    data["end_time"] = ts

                if dist is not None:
                    data["total_distance_m"] = max(data["total_distance_m"], dist)

                if speed is not None and speed > 0:
                    speeds.append(speed)
                    data["max_speed_mps"] = max(data["max_speed_mps"], speed)

                if hr is not None and hr > 0:
                    heart_rates.append(hr)
                    data["max_heart_rate"] = max(data["max_heart_rate"], hr)

                if alt is not None:
                    alt = float(alt)
                    altitudes.append(alt)
                    data["min_altitude"] = min(data["min_altitude"], alt)
                    data["max_altitude"] = max(data["max_altitude"], alt)
                    if last_altitude is not None:
                        diff = alt - last_altitude
                        # 气压计小波动滤波：< 1m 的跳动不计入爬升/下降
                        if abs(diff) >= 1.0:
                            if diff > 0:
                                data["elev_gain"] += diff
                            else:
                                data["elev_loss"] += abs(diff)
                    last_altitude = alt

                if cad is not None and cad < 255:
                    cadence_values.append(cad)
                    data["max_cadence"] = max(data["max_cadence"], cad)

                if cal is not None:
                    data["total_calories"] = max(data["total_calories"], cal)

                if temp is not None:
                    data["temperature_c"].append(temp)

            elif name == "weather_conditions":
                ws = field_val("wind_speed")
                wd = field_val("wind_direction")
                rh = field_val("relative_humidity")
                if ws is not None:
                    data["wind_speed_mps"] = ws
                if wd is not None:
                    data["wind_direction_deg"] = wd
                if rh is not None:
                    data["humidity"] = rh

    # 计算均值
    if speeds:
        data["avg_speed_mps"] = sum(speeds) / len(speeds)
    if heart_rates:
        data["avg_heart_rate"] = sum(heart_rates) / len(heart_rates)
    if cadence_values:
        data["avg_cadence"] = sum(cadence_values) / len(cadence_values)

    # 计算时长
    if data["start_time"] and data["end_time"]:
        delta = data["end_time"] - data["start_time"]
        data["total_elapsed_s"] = delta.total_seconds()

    return data


# ====== 工具函数 ======

def format_beijing_time(dt):
    """将 UTC datetime 转换为北京时间 (+8h) 并格式化"""
    if not dt:
        return ""
    from datetime import timezone, timedelta
    bj_tz = timezone(timedelta(hours=8))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(bj_tz).strftime('%Y-%m-%d %H:%M')


# ====== 智能命名 ======

def get_title(d: dict) -> str:
    """根据运动类型、数据、时间生成标题"""
    from datetime import timezone, timedelta
    sport = d["sport"]
    sub = d["sub_sport"]
    dist = d["total_distance_m"]
    dist_km = dist / 1000
    start_time = d["start_time"]

    # 判断骑行时间段
    ride_time = ""
    if start_time:
        bj_tz = timezone(timedelta(hours=8))
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        local = start_time.astimezone(bj_tz)
        hour = local.hour
        if 5 <= hour < 9:
            ride_time = "晨骑"
        elif 9 <= hour < 12:
            ride_time = "午前骑"
        elif 12 <= hour < 14:
            ride_time = "午骑"
        elif 14 <= hour < 18:
            ride_time = "午后骑"
        elif 18 <= hour < 21:
            ride_time = "夜骑"
        else:
            ride_time = "夜骑"

    # 骑行标题：只用心率区间美化名称（不加时间/里程/地点）
        if sport == "Ride":
            hr_zone = get_hr_zone(d["avg_heart_rate"], d["max_heart_rate"]) if d["max_heart_rate"] > 0 else ""
            return hr_zone
    elif sport == "Run":
        dist_km = dist / 1000
        avg_hr = d["avg_heart_rate"]
        max_hr = d["max_heart_rate"]
        hr_zone = get_hr_zone(avg_hr, max_hr) if max_hr > 0 else ""
        hr_label = f" · {hr_zone}" if hr_zone else ""
        if dist_km < 3:
            base = "晨跑" if ride_time and "晨" in ride_time else "短跑"
        elif dist_km < 10:
            base = "中长跑训练"
        else:
            base = "长跑拉练"
        return base + hr_label
    elif sport == "Walk":
        return "徒步健行"
    elif sport == "Swim":
        return "游泳训练"
    else:
        return f"{sub}" if sub else sport


def get_description(d: dict) -> str:
    """生成详细的数据分析描述"""
    lines = []

    dist = d["total_distance_m"]
    dist_km = dist / 1000
    avg_speed = d["avg_speed_mps"]
    max_speed = d["max_speed_mps"]
    avg_hr = d["avg_heart_rate"]
    max_hr = d["max_heart_rate"]
    elev_gain = d["elev_gain"]
    elev_loss = d["elev_loss"]
    duration_s = d["total_elapsed_s"]
    calories = d["total_calories"]
    cadence_vals = [v for v in [d.get("avg_cadence", 0)] if v > 0]
    avg_cadence = d.get("avg_cadence", 0)
    temps = d["temperature_c"]
    max_alt = d["max_altitude"]
    min_alt = d["min_altitude"]
    bike = d["bike_name"]
    sport = d["sport"]
    sub = d["sub_sport"]

    # 时长格式化
    h = int(duration_s // 3600)
    m = int((duration_s % 3600) // 60)
    s = int(duration_s % 60)
    if h > 0:
        dur_str = f"{h}小时{m}分"
    else:
        dur_str = f"{m}分{s}秒"

    lines.append("📊 运动数据摘要")
    lines.append("─" * 20)
    lines.append(f"⚡ 时长: {dur_str}")
    lines.append(f"📏 里程: {dist_km:.2f} km")
    lines.append(f"🚴 平均速度: {avg_speed * 3.6:.1f} km/h")
    lines.append(f"🔥 最高速度: {max_speed * 3.6:.1f} km/h")

    if sport == "Ride" and avg_cadence > 0:
        lines.append(f"🔄 平均踏频: {int(avg_cadence)} rpm")

    if avg_hr > 0:
        lines.append(f"❤️ 平均心率: {int(avg_hr)} bpm")
        lines.append(f"💓 最高心率: {int(max_hr)} bpm")

        # 心率区间分析
        hr_zone = get_hr_zone(avg_hr, max_hr)
        lines.append(f"🏷️ 心率区间: {hr_zone}")

    if elev_gain > 0:
        lines.append(f"⬆️ 爬升: {elev_gain:.0f} m")
    if elev_loss > 0:
        lines.append(f"⬇️ 下降: {elev_loss:.0f} m")
    if max_alt < 99999 and min_alt > 0:
        lines.append(f"⛰️ 海拔范围: {min_alt:.0f}m ~ {max_alt:.0f}m")

    if calories > 0:
        lines.append(f"🔥 热量: {calories} kcal")

    if temps:
        # 去掉前2个和最后1个（开关机异常值）
        clean_temps = temps[2:-1] if len(temps) > 3 else temps
        if clean_temps:
            avg_temp = sum(clean_temps) / len(clean_temps)
            lines.append(f"🌡️ 平均气温: {avg_temp:.0f}°C")

    if d["wind_speed_mps"] > 0:
        wind_kmh = d["wind_speed_mps"] * 3.6
        wind_dir = compass_direction(d["wind_direction_deg"])
        lines.append(f"💨 风向风速: {wind_dir} {wind_kmh:.1f} km/h")

    lines.append("─" * 20)

    # 综合评价
    lines.append("📝 训练评价")
    lines.append("─" * 20)

    if sport == "Ride":
        评论 = []
        if avg_speed * 3.6 > 30:
            评论.append("速度表现优秀🔥")
        elif avg_speed * 3.6 > 20:
            评论.append("巡航节奏稳定")
        if elev_gain > 200:
            评论.append("爬坡训练扎实")
        if duration_s > 7200:
            评论.append("长距离耐力训练")
        if calories > 500:
            评论.append(f"消耗热量{calories}kcal，燃脂效果好")

        if bike:
            评论.append(f"爱车: {bike}")

        lines.extend(评论 if 评论 else ["完成一次训练，继续保持💪"])

    elif sport == "Run":
        评价 = []
        if dist_km > 10:
            评价.append("长跑训练完成，距离感人的意志力👍")
        elif dist_km > 5:
            评价.append("有氧耐力训练，维持好状态")
        if avg_hr > 0 and avg_hr < 130:
            评价.append("心率稳定，训练强度适中")
        elif avg_hr > 0:
            评价.append("高强度训练，注意恢复")

        lines.extend(评价 if 评价 else ["跑步训练完成💪"])

    # 设备信息
    if d["product_name"]:
        lines.append(f"📱 码表: {d['product_name']}")

    lines.append(f"🤖 自动生成 | {format_beijing_time(d['start_time'])}")

    return "\n".join(lines)


def get_hr_zone(avg_hr, max_hr) -> str:
    """简单心率区间估算（按最大心率法）"""
    if max_hr <= 0:
        return "未知"
    pct = avg_hr / max_hr * 100
    if pct < 60:
        return "Z1 轻松区"
    elif pct < 70:
        return "Z2 脂肪燃烧区"
    elif pct < 80:
        return "Z3 有氧耐力区"
    elif pct < 90:
        return "Z4 乳酸阈值区"
    else:
        return "Z5 最大摄氧区"


def compass_direction(deg: float) -> str:
    """度数转罗盘方向"""
    directions = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
    idx = int((deg + 22.5) // 45) % 8
    return directions[idx]


# ====== Strava API ======

def refresh_access_token():
    tok = load_token()
    resp = requests.post(TOKEN_URL, data={
        "client_id": CLIENT_ID,
        "client_secret": tok.get("client_secret"),
        "grant_type": "refresh_token",
        "refresh_token": tok.get("refresh_token"),
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    # 保存新 token
    data["client_secret"] = tok.get("client_secret")
    save_token(data)
    print(f"[*] Token 刷新成功，过期时间: {data.get('expires_at', 'N/A')}")
    return data["access_token"]


def upload_fit(access_token, fit_path, activity_type, name, description):
    if not os.path.exists(fit_path):
        print(f"[×] 文件不存在: {fit_path}")
        sys.exit(1)

    file_size = os.path.getsize(fit_path)
    print(f"[i] 文件大小: {file_size / 1024:.1f} KB")

    # gzip 压缩（与 strava_auto 行为一致）
    with open(fit_path, "rb") as f:
        raw_bytes = f.read()
    compressed = gzip.compress(raw_bytes, compresslevel=6)
    print(f"[i] gzip 压缩后: {len(compressed) / 1024:.1f} KB")

    # 生成新的文件名（原名.fit.gz）
    base_name = os.path.basename(fit_path)
    ext_idx = base_name.rfind('.')
    gz_name = base_name[:ext_idx] + '.fit.gz' if ext_idx > 0 else base_name + '.fit.gz'

    files = {"file": (gz_name, compressed, "application/gzip")}
    data = {
        "name": name or os.path.basename(fit_path).replace(".fit", ""),
        "description": description or "",
        "activity_type": activity_type,
        "data_type": "fit.gz",
    }
    headers = {"Authorization": f"Bearer {access_token}"}

    print(f"[i] 上传中 ({activity_type}) ...")
    resp = requests.post(
        UPLOAD_URL, headers=headers, data=data, files=files, timeout=60,
    )

    resp.raise_for_status()
    result = resp.json()

    if result.get("error"):
        print(f"[×] 上传失败: {result}")
        sys.exit(1)

    upload_id = result.get("id")
    activity_id = result.get("activity_id")
    status = result.get("status")

    print(f"[√] 上传已提交!")
    print(f"    Upload ID : {upload_id}")
    print(f"    Status    : {status}")
    if activity_id:
        print(f"    Activity  : https://www.strava.com/activities/{activity_id}")
    else:
        print(f"[i] 活动 ID 暂时未知（后台处理中）")

    if upload_id:
        print("[i] 等待后台处理...")
        for i in range(12):
            time.sleep(5)
            check = requests.get(
                f"{UPLOAD_URL}/{upload_id}", headers=headers, timeout=15,
            )
            sd = check.json()
            a = sd.get("activity_id")
            s = sd.get("status", "")
            print(f"    [{i+1}] {s}" + (f" → https://www.strava.com/activities/{a}" if a else ""))
            if a or sd.get("error"):
                break


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Strava FIT Upload Script")
    parser.add_argument("fit_file", nargs="?", help="FIT 文件路径")
    parser.add_argument("activity_type", nargs="?", help="运动类型 (Run/Ride/...)")
    parser.add_argument("title", nargs="?", help="自定义标题")
    parser.add_argument("--correct-coords", action="store_true",
                        help="上传前将 GCJ-02 坐标纠偏为 WGS-84")
    parser.add_argument("--parse-only", dest="parse_only", action="store_true",
                        help="仅解析 FIT 文件，输出 JSON 后退出")
    args = parser.parse_args()

    if args.parse_only:
        if not args.fit_file:
            print("错误: --parse-only 需要指定 FIT 文件路径")
            sys.exit(1)
        d = parse_fit(args.fit_file)
        print(json.dumps(d, default=str, ensure_ascii=False))
        sys.exit(0)

    if not args.fit_file:
        print("用法: python3 strava-upload.py <fit文件> [activity_type] [title] [--correct-coords]")
        print(f"可选 activity_type: {', '.join(ACTIVITY_TYPES)}")
        print("       python3 strava-upload.py --parse-only <fit文件>  # 仅解析输出JSON")
        print("       python3 strava-upload.py <fit文件> --correct-coords  # 上传前纠偏坐标")
        sys.exit(1)

    print(f"[*] 解析 FIT 文件: {args.fit_file}")
    d = parse_fit(args.fit_file)

    # 自动确定 activity_type
    activity_type = args.activity_type if args.activity_type else d["sport"]
    if activity_type not in ACTIVITY_TYPES:
        activity_type = d["sport"] if d["sport"] in ACTIVITY_TYPES else "Ride"

    # 手动传入的标题优先，否则自动生成
    title = args.title.strip() if args.title else get_title(d)
    desc = get_description(d)

    print(f"\n[*] 智能分析结果:")
    print(f"    运动类型: {activity_type} ({d['sub_sport']})")
    print(f"    标题: {title}")
    print(f"    描述:\n{desc}")

    # 坐标纠偏（如需要）
    fit_path = args.fit_file
    if args.correct_coords:
        print(f"\n[*] 正在进行 GCJ-02 → WGS-84 坐标纠偏...")
        try:
            corrected = correct_fit_file(fit_path)
            # 原地替换，下次直接用纠偏后文件
            import shutil
            tmp = fit_path + '.tmp'
            shutil.move(corrected, tmp)
            shutil.move(fit_path, fit_path + '.orig')
            shutil.move(tmp, fit_path)
            print(f"    纠偏完成: {fit_path}")
        except Exception as e:
            print(f"    纠偏失败: {e}，使用原始文件继续...")

    print(f"\n[*] 开始上传...")

    # 优先使用缓存 token，未过期直接用
    tok = load_token()
    access_token = tok.get("access_token", "")
    expires_at = tok.get("expires_at", 0)
    import datetime
    now = time.time()
    if access_token and expires_at > now:
        print(f"[*] 使用缓存 Token（过期时间: {datetime.datetime.fromtimestamp(expires_at).strftime('%Y-%m-%d %H:%M')})")
    else:
        print("[*] Token 已过期，刷新中...")
        access_token = refresh_access_token()

    upload_fit(access_token, fit_path, activity_type, title, desc)


if __name__ == "__main__":
    main()
