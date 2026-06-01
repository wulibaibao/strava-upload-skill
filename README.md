# strava-upload Skill（导出版 · 2026-06-01）

完整打包 FIT → Strava 工作流。包含**主上传脚本**、**GPS 漂移修复方案**、**GCJ-02 纠偏**、**GPX 中介法**等全部实战经验。

> 仓库地址：https://github.com/wulibaibao/strava-upload-skill
>
> **MAGENE C706 实测输出 GCJ-02**（不是 WGS-84）— 见 `references/magene-c706-gcj02-offset.md`

## 快速使用（3 步上手）

```bash
# 1. 装依赖
pip install -r requirements.txt --break-system-packages

# 2. 准备 Strava token（首次）
#    见 references/strava-upload-status-check.md 里的 OAuth 流程
#    写入 ~/.hermes/strava_token.json（access_token / refresh_token / expires_at）

# 3. 上传
python3 scripts/strava-upload.py /path/to/file.fit Ride
```

**可选 flag**：
- `--correct-coords` — 国产码表输出 GCJ-02 时转换 WGS-84
- `<title>` 第二个位置参数 — 自定义活动名（默认用心率区间，如 "Z4 · 乳酸阈值区"）

**核心触发词**：上传到 Strava、上传 FIT 文件、strava 上传

## 目录结构

```
strava-upload/
├── SKILL.md              # 主入口：上传、标题、GPS 状态、坑汇总（22 KB）
├── README.md             # 本文件
├── requirements.txt      # 依赖清单
├── references/           # 详细参考文档（按需阅读，共 10 篇 / 47 KB）
│   ├── fit-method-comparison.md           ⭐ 决策树：偏移类型→修复方法
│   ├── strava-gcj02-wgs84-correction.md   ⭐ GCJ-02→WGS-84 转换 + 二进制 patch
│   ├── gpx-intermediary-method.md         ⭐ GPX 中介法（最可靠）
│   ├── fit-rebuild-encoder.md             FIT 重建 Encoder 方案
│   ├── osm-snap-to-road-analysis.md       OSM 路网偏差分析
│   ├── two-segment-gps-drift.md           两段式 GPS 漂移识别
│   ├── magene-c706-gcj02-offset.md        GCJ-02 系统偏移识别
│   ├── check_fit_gps.md                   GPS 质量快速检查
│   ├── strava-upload-status-check.md      上传状态查询/重复检测
│   └── email-delivery-architecture.md     Hermes 邮件双通道陷阱
└── scripts/              # 可执行脚本（3 个 / 41 KB）
    ├── strava-upload.py                   主入口：上传 FIT → Strava（903 行）
    ├── correct_gps.py                     两段式漂移修复
    └── detect_gcj02_offset.py             GCJ-02 偏移一键诊断
```

## 关键文档阅读顺序（遇到问题时）

1. **路线不对/位置不对** → 先读 `references/fit-method-comparison.md` 决策树
2. **GCJ-02 偏移（恒定方向/距离偏 300-500m）** → `references/magene-c706-gcj02-offset.md` + `references/strava-gcj02-wgs84-correction.md`
3. **两段式漂移（前段正常后段突然偏）** → `references/two-segment-gps-drift.md` + `scripts/correct_gps.py`
4. **设备完全失锁（坐标飞掉）** → `references/fit-rebuild-encoder.md` 或 `references/gpx-intermediary-method.md`
5. **不确定是真偏还是路网盲区** → `references/osm-snap-to-road-analysis.md` + `scripts/detect_gcj02_offset.py`

## 关键技术原理

### 1. 国产码表的 GCJ-02 偏移（中国法定坐标系）

**问题**：中国法律规定境内地图必须用 GCJ-02（火星坐标），但 Strava / Google Maps / OSM 国际版都用 WGS-84。MAGENE C706 / 行者 / 顽鹿 等国产码表**默认输出 GCJ-02**，上传 Strava 后地图会恒定方向偏 300-500m。

**识别方法**（5-10 min 确诊）：
- GPS 点当 WGS-84 vs WGS-84 路网 → 中位偏差 A
- GPS 点当 GCJ-02 vs GCJ-02 路网 → 中位偏差 B
- 若 B < 10m 且 A > 50m → **确认 GCJ-02 偏移**

**修复**：二分迭代 GCJ-02→WGS-84 算法（参考 `references/strava-gcj02-wgs84-correction.md`），再用 `fit-tool` 或二进制 patch 改写 FIT 里的 record 坐标字段（注意重建 CRC-16 校验和）。

### 2. Encoder 重建 FIT 必丢元数据

**陷阱**：`garmin_fit_sdk.Encoder` 设计上**只复制 Decoder 解析过的消息**。原文件的 `file_id` / `timestamp_correlation` / `device_info` / `capabilities` 等元数据消息如果没有显式透传给 Encoder，输出文件**只含 record 数据**——Strava 收到后直接拒绝（"There was an error processing your activity"）。

**解决方案**：
- 方案 A：**GPX 中介法**（推荐）— `fitdecode` → GPX → `Encoder`，只写 `record` + `session` + `activity` 三种 essential 消息，丢弃所有厂商特定字段
- 方案 B：保留原文件所有非 record 消息 → Encoder 透传 → 只改 record 坐标

**判断**：Encoder 输出的 FIT 上传 Strava 报错 → 几乎肯定是元数据丢失。

### 3. GPX 中介法为什么比直传 Encoder 稳

直传 Encoder 经常因为厂商字段（`developer_data_id` / `field_description` / `unknown_255`）转换失败；GPX 作为通用交换格式是 schema-clean 的，从 GPX 写 FIT 时只保留 essential 消息，**避开了所有厂商特定字段**。代价是丢失 `distance` 等聚合字段（需在 Encoder 写入时手动 inject `record['distance']`）。

### 4. Strava DEM 海拔膨胀

**不是 bug，是 Strava 行为**：Strava 用自己的 ASTER DEM 数字高程模型**覆盖** FIT 原始海拔。FIT 记录 1140m 爬升，Strava 显示 3664m（3.2×）。无法通过重建 FIT 修复，只能在网页端 `Edit → Adjust Elevation` 关闭 DEM 校正。

### 5. 海量定位陷阱：fitdecode vs garmin_fit_sdk

| 场景 | 工具 | 关键陷阱 |
|------|------|---------|
| **只读 record** | `fitdecode.FitReader` | `chunk.get('lat')` AttributeError！正确是 `getattr(chunk, f.name, None)` 或 `f.value` |
| **重建 FIT** | `garmin_fit_sdk.Encoder` | 必丢元数据（见 §2），且 `Profile['messages']` 默认是 str key，必须 `int(k)` 转换 |
| **改二进制坐标** | `fit-tool` 或手写 patch | Record/Lap/Session/CoursePoint/SegmentPoint 5 个字段都要改 + 重建 CRC-16 |
| **坐标转换** | `semicircles / (2**32/360)` | 单位 1/299792458 秒，不要直接当 int 经纬度 |

## 关键事实（2026-06-01 版）

- **MAGENE C706 实际输出 GCJ-02**（不是 WGS-84）。`--correct-coords` 默认会纠偏
- **默认不主动做偏移诊断**，直接上传。仅在用户主动反馈"路线不对"时诊断
- **自动标题格式**：只用心率区间（Z1~Z5），不加时间/里程/地点
- **Encoder 重建 FIT 必丢元数据**：必须用 Decoder 的 mesg_listener 把所有消息原样透传，或用 GPX 中介法
- **Strava DEM 海拔膨胀**：不是 bug，Strava 用自己的 ASTER DEM，FIT 原始海拔会被覆盖

## 已知限制

- **Strava OAuth scope** 不含 `activity:delete`，无法通过 API 删除活动（需网页操作）
- **`gh` CLI** 在某些环境未装，本 skill 的 GitHub 集成通过 REST API + curl 实现
- **MAGENE C706 星历**有效期约 2-4 小时，每次骑前需用顽鹿运动 App 同步

## License

MIT
