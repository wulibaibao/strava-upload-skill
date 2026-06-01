# strava-upload Skill（导出版 · 2026-06-01）

完整打包 FIT → Strava 工作流。包含**上传脚本**、**GPS 漂移修复方案**、**GCJ-02 纠偏**、**GPX 中介法**等全部实战经验。

## 快速使用

```bash
# 1. 安装依赖
pip install requests fitdecode garmin-fit-sdk -q --break-system-packages

# 2. 把脚本放到工作目录
cp scripts/correct_gps.py /home/<user>/
cp scripts/detect_gcj02_offset.py /home/<user>/

# 3. 上传 FIT
python3 /home/<user>/strava-upload.py /path/to/file.fit Ride
```

**核心触发词**：上传到 Strava、上传 FIT 文件、strava 上传

## 目录结构

```
strava-upload/
├── SKILL.md              # 主入口：上传、标题、GPS 状态、坑汇总
├── README.md             # 本文件
├── references/           # 详细参考文档（按需阅读）
│   ├── fit-method-comparison.md       ⭐ 决策树：偏移类型→修复方法
│   ├── strava-gcj02-wgs84-correction.md  ⭐ GCJ-02→WGS-84 转换 + 二进制 patch
│   ├── gpx-intermediary-method.md     ⭐ GPX 中介法（最可靠）
│   ├── fit-rebuild-encoder.md         FIT 重建 Encoder 方案
│   ├── osm-snap-to-road-analysis.md   OSM 路网偏差分析
│   ├── two-segment-gps-drift.md       两段式 GPS 漂移识别
│   ├── magene-c706-gcj02-offset.md    GCJ-02 系统偏移识别
│   ├── check_fit_gps.md               GPS 质量快速检查
│   ├── strava-upload-status-check.md  上传状态查询/重复检测
│   └── email-delivery-architecture.md  Hermes 邮件双通道陷阱
└── scripts/              # 可执行脚本
    ├── correct_gps.py                # 两段式漂移修复
    └── detect_gcj02_offset.py        # GCJ-02 偏移一键诊断
```

## 关键文档阅读顺序（遇到问题时）

1. **路线不对/位置不对** → 先读 `fit-method-comparison.md` 决策树
2. **GCJ-02 偏移（恒定方向/距离偏 300-500m）** → `magene-c706-gcj02-offset.md` + `strava-gcj02-wgs84-correction.md`
3. **两段式漂移（前段正常后段突然偏）** → `two-segment-gps-drift.md` + `scripts/correct_gps.py`
4. **设备完全失锁（坐标飞掉）** → `fit-rebuild-encoder.md` 或 `gpx-intermediary-method.md`
5. **不确定是真偏还是路网盲区** → `osm-snap-to-road-analysis.md` + `scripts/detect_gcj02_offset.py`

## 关键事实（2026-06-01 版）

- **MAGENE C706 实际输出 GCJ-02**（不是 WGS-84）。`--correct-coords` 默认会纠偏
- **06-01 起用户偏好**：默认不主动做偏移诊断，直接上传。仅在用户主动反馈"路线不对"时诊断
- **自动标题格式**：只用心率区间（Z1~Z5），不加时间/里程/地点
- **Encoder 重建 FIT 必丢元数据**：必须用 Decoder 的 mesg_listener 把所有消息原样透传，或用 GPX 中介法
- **Strava DEM 海拔膨胀**：不是 bug，Strava 用自己的 ASTER DEM，FIT 原始海拔会被覆盖

## License

MIT
