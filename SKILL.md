---
name: strava-upload
description: 上传 FIT 文件到 Strava，支持自动刷新 token、activity_type 指定、活动名和描述。触发词：上传到Strava、上传FIT文件、strava上传。直接传入 fit 文件路径即可，不支持 GPX。
license: MIT
version: 1.3.0
---

# Strava FIT 文件上传

上传 FIT 文件到 Strava，支持自动刷新 token、activity_type 指定、活动名和描述。

## 使用方法

```bash
python3 /home/agentuser/strava-upload.py <fit文件路径> [activity_type] [title] [--correct-coords]
```

**参数说明：**
- `fit文件路径` — 必填，.fit 文件的绝对路径
- `activity_type` — 可选，默认 `Ride`
- `title` — 可选，活动名称，默认自动生成（见下「自动标题格式」）
- `--correct-coords` — 上传前将 FIT 内的 GCJ-02 坐标纠偏为 WGS-84（用于国产设备输出的 FIT）

**支持的 activity_type：** Run, Ride, Swim, Walk, Hike, Workout, WeightTraining, Yoga

## 依赖

```bash
pip install requests -q --break-system-packages
```

## 上传后质量检查（主动报告，不要等用户问）

上传成功后**立即抓取活动详情**并报告关键指标，不要只说"上传成功"。

**自动标题格式（骑行，06-01 起固定）：**
只用心率区间美化名称作为标题，**不加时间/里程/地点**：
- Z1: < 120 bpm → 热身区
- Z2: 120–140 bpm → 轻松区
- Z3: 140–152 bpm → 马拉松区
- Z4: 152–164 bpm → 乳酸阈值区
- Z5: > 164 bpm → 极限区

例：avg_hr=141, max_hr=166 → Z3 → 标题「Z3 · 马拉松区」

**修改记录：**
- 06-01：骑行标题从「06-01 西安晨骑 54km」改为只用心率区间名称。用户明确说"不需要加时间/里程/位置，只需要美化一下心率区间"。`strava-upload.py` 的 `get_title()` 已同步更新（删除了 ride_time 前缀）。

**修改已上传活动的标题：**
```python
import json, requests
tok = json.load(open('/home/agentuser/.hermes/strava_token.json'))
r = requests.patch(
    f'https://www.strava.com/api/v3/activities/{activity_id}',
    headers={'Authorization': f'Bearer {tok["access_token"]}'},
    json={'name': 'Z4 · 乳酸阈值区'}
)
```
返回 200 + 完整 activity 对象即成功。

```python
import json, requests
tok = json.load(open('/home/agentuser/.hermes/strava_token.json'))
headers = {'Authorization': f'Bearer {tok["access_token"]}'}
r = requests.get(f'https://www.strava.com/api/v3/activities/{activity_id}', headers=headers)
a = r.json()
print(f'距离: {a["distance"]/1000:.2f} km')
print(f'移动时间: {a["moving_time"]/3600:.2f} h')
print(f'总爬升: {a["total_elevation_gain"]} m')
print(f'avg speed: {a["average_speed"]*3.6:.1f} km/h')
print(f'起点: {a["start_latlng"]}')
print(f'终点: {a["end_latlng"]}')
```

**报告格式（一句话概述 + 关键数据）：**
- 正常：「✅ 已上传。距离 144.83 km，移动时间 5.01 h，总爬升 3663 m（⚠️ 偏高需确认），平均速度 28.9 km/h，起点终点已回到原点。」
- 有问题：「⚠️ 已上传但数据异常：[具体问题]」

**快速判断标准：**
- 距离：±10% 预期值正常
- 移动速度：骑行 20-35 km/h 合理
- 爬升：145 km 骑行合理值约 1500-2500 m；>3000 m 明显偏高，可能是 GPS 高度漂移未完全修复
- 坐标：西安地区应在 `(34.2, 108.8)` ±0.05°，不是 `(68, 217)`

## 已知问题

### 上传报错 "duplicate" 但文件已上传成功

Strava 重复检测逻辑：即使返回 `error: "duplicate of /activities/XXX"` 且 `status: "There was an error processing your activity."`，**活动实际上已经被创建**了（见 `activity_id` 字段判断）。

去 Strava 手动确认一下是否有对应活动，如有则无需重复上传。错误原因很可能是 GPS 手表记录的时间和 Strava 处理后的 UTC 时间存在 1 天偏差（比如北京 05-23 06:07 的活动显示为 UTC 05-22 22:07），导致 Strava 认为是不同日期的重复文件。

**判断是否已上传：**
```python
import json, requests
tok = json.load(open('/home/agentuser/.hermes/strava_token.json'))
headers = {'Authorization': f'Bearer {tok["access_token"]}'}
resp = requests.get(f'https://www.strava.com/api/v3/uploads/{upload_id}', headers=headers)
data = resp.json()
# error 字段包含 "duplicate of /activities/" 说明已上传成功，只是被标记为重复
# 无需再次上传，直接去 Strava 查看活动链接即可
```

### activity_type 选错会导致上传失败

`Run`（跑步）和 `Ride`（骑行）不要填错。运动手表解析出来的类型不一定准确，骑行活动传 Run 会报错处理失败。

### ⚠️ Encoder 重建 FIT 文件丢失元数据的问题（核心陷阱！）

**问题描述：** 用 `garmin_fit_sdk Encoder` 读取 record 数据并重建 FIT 时，Encoder 只写入了 record 消息，丢失了原始文件的 `file_id`、`timestamp`、`session`、`activity` 等所有元数据消息。Strava 收到这样的文件后返回 **"There was an error processing your activity."**，活动实际未被创建。

**根本原因：** Encoder 设计上只复制有对应 Decoder 支持的字段，metadata 消息（如 file_id、device_info）没有经过 Decoder → 就不会写入输出文件。

**正确流程：保留原文件所有非 record 元数据。** 具体见 `references/fit-rebuild-encoder.md` — 关键是：
1. 用 Decoder 遍历原始文件所有消息，对 record 消息做坐标校正再写入 Encoder
2. 对非 record 消息（file_id, session, etc.），原样透传给 Encoder
3. 这样输出的 FIT 保留了原始文件的全部元数据，Strava 就能正确处理

**症状判断：** 重建后的 FIT 上传 Strava 报错 "There was an error processing your activity" → 几乎肯定是Encoder 输出缺少元数据导致的。

**经验记录（05-30 案例）：**
- Decoder+Encoder 输出的 FIT 在 `file_id` 和 `timestamp` 等元数据字段上是完整的（`garmin_fit_sdk` 会写入），但某些运动手表的厂商特定字段缺失可能导致 Strava 解析失败
- 如果 Encoder 输出仍报错，需要尝试保留原始文件结构 + 逐条替换 record 数据（见 `references/fit-rebuild-encoder.md` 的"保留原始元数据"方案）

**✅ GPX 中介法（05-30 验证通过，最可靠）：**

当 Encoder 直接写入困难时，改为 GPX 中介路径：

1. `fitdecode.FitReader` 解析原始 FIT → 提取所有 record（lat/lon/alt/hr/cad/spd/pwr/timestamp）
2. 转 semicircles→degrees，排序，写 GPX 文件（含 extensions 里 hr/cad/spd/pwr）
3. 从 GPX 读回 record 数据，写 `garmin_fit_sdk.Encoder`
4. 只保留 essential 元数据（`file_id`, `session`, `activity`），**其他全部丢弃**（`capabilities`/`developer_data_id`/`field_description` 等有厂商特定字段，容易转换失败）
5. 上传重建的 FIT

关键代码模式（已验证）：
```python
from garmin_fit_sdk.profile import Profile
Profile['messages'] = {int(k): v for k, v in Profile['messages'].items()}  # 修复 str key bug

mesg_name_to_num = {}
for k, v in Profile['messages'].items():
    name = v.get('name', '').lower()
    num = v.get('num')
    if isinstance(num, str): num = int(num)
    if name and num is not None:
        mesg_name_to_num[name] = int(num)

encoder.write_mesg({'mesg_num': mesg_name_to_num['record'], 'position_lat': semicircles(lat), 'position_long': semicircles(lon), 'timestamp': ts, ...})
```

注意：GPX 中介法会丢失 `distance` 字段导致 Strava 显示 0 km。**已修复：** Step 1 fitdecode 提取时 distance 已在 record dict；Step 4 Encoder 写入时 inject `record['distance'] = int(r['distance'])` 即可。

### API Token 缺少 activity:delete 权限

当前 Strava OAuth token 的 scope 是 `activity:read_all activity:write read`，**没有** `activity:delete`。因此无法通过 API 删除活动。

**表现：** `DELETE /api/v3/activities/{id}` 返回 `401 {"message":"Authorization Error","errors":[{"resource":"Application","field":"internal","code":"invalid"}]}`。

**变通：** 手动在 Strava 网页删除对应活动，然后立即重新上传。

## 水平偏移必须区分（四类，含 2026-06-01 关键修订）

| 类型 | 表现 | 原因 | 是否需修复 |
|------|------|------|----------|
| **GPS设备漂移（全失锁）** | 坐标飞到 `(68°N, 217°E)` | 星历过期，GPS完全失锁 | ✅ 是（Encoder 重建） |
| **两段式漂移** | 前段正常，后段突然整体偏移 0.1-0.5°（数十km），无过渡 | GPS信号短暂中断后重锁，锁定在错误位置 | ✅ 是（Encoder 修复偏移段） |
| **GCJ-02 偏移** ⚠️ | 路线**全程恒定方向、恒定距离**偏 300-500m | 国产码表（含 **MAGENE C706**）输出 GCJ-02 而非 WGS-84 | ✅ 是（GCJ-02→WGS-84 转换后重传） |
| **Strava 地图匹配** | 路线整体偏 30-100m | Strava 自己的算法 | ❌ 否（Strava 行为） |

**⚠️ 2026-06-01 关键修订：旧参考说 "MAGENE C706 输出 WGS-84，50-300m 偏移是 Strava 地图匹配无需修复" —— 这是错的！** 实测 05-30 骑行 OSM 偏差分析证明 C706 输出 GCJ-02（路线 vs WGS-84 路网中位偏 72m，方向恒东偏南；转 GCJ-02 路网后骤降到 4.8m）。详见 `references/magene-c706-gcj02-offset.md`（已重写）。

**判断方法**：用户说"地图偏移不对"时，按以下顺序排查：
1. **方向恒定 + 距离恒定 + 用户说"固定偏移"** → **GCJ-02 偏移**（国产码表默认行为）→ 用 `--correct-coords` 转换后重传
2. **FIT 坐标 >50° / >150°** → 设备漂移（完全失锁）→ Encoder 重建
3. **前段正常 + 后段突然整体偏移 0.1-0.5°，无过渡** → 两段式漂移 → Encoder 修复偏移段
4. **孤立跳点（前后都 < 50m，中间突然 > 1km）** → GPS 跳点 → fitdecode 删除跳点
5. **路线形状正确仅偏 30-100m** → Strava 地图匹配，无需修

**GCJ-02 确诊方法（最可靠）**：跑 OSM 偏差分析，把 OSM 路网**同时**转成 WGS-84 和 GCJ-02 两个版本，GPS 点跟两个版本路网都比一遍：
- "GPS 当 WGS-84 vs WGS-84 路网" 中位偏差大（如 50-500m）
- "GPS 当 GCJ-02 vs GCJ-02 路网" 中位偏差骤降到 < 10m
→ **确诊 GPS 输出 GCJ-02**

**注意（06-01 用户偏好）：** 用户说"不需要再管偏移了"——**默认情况下不主动分析 GPS 漂移**，直接上传。但用户**主动反馈"路线不对"/"位置不对"/"固定偏移"**时仍需做 OSM + GCJ-02 双向验证（这是 30 秒内就能排除主要假阳性的方法）。

### QQ 附件缓存位置（关键！）

如果 Strava 上显示的路线跟实际路线差很多，问题**不在 Strava**，而是 GPS 手表记录的原始坐标就有漂移（如 MAGENE C706 信号飘移）。重新上传同一文件会被 Strava 判定为重复文件拒绝处理。

**解决方法：用 Encoder 重建干净 FIT 文件再上传**

见 `references/fit-rebuild-encoder.md` — 用 fitdecode 读取 + garmin-fit-sdk Encoder 重建，绕过 external_id 重复检测。

1. 删除 Strava 上原有的问题活动（API 无 delete 权限，需网页操作）
2. 用 `/home/agentuser/convert_fit_encoder.py` 重建 FIT 文件（venv python：`/home/agentuser/.hermes/hermes-agent/venv/bin/python`）
3. 上传到 Strava

**2026-06-01 新增更简单方案：** 用 `fit-tool` 直接 patch 原始 FIT 文件的 record 坐标（保留所有元数据），无需重建：
```bash
fit-tool update <file.fit> --lat <new_lat_semicircles> --lon <new_lon_semicircles>
```
详见 `references/fit-rebuild-encoder.md` 的 fit-tool 方案。

### 海拔膨胀问题（Strava DEM 自动校正）

**不是 bug，是 Strava 正常行为。**

Strava 会对 FIT 文件的海拔数据进行**自己的数字高程模型（DEM）校正**，不使用 FIT 原始海拔。校正后：
- FIT 原始爬升 1140 m → Strava 显示 3664 m（**3.2 倍膨胀**）
- FIT 记录海拔 362~954 m → Strava 显示 394~974 m（整体平移 + 拉伸）

**这是 Strava 的标准流程，无法通过重建 FIT 修复。** Strava 使用自己的 ASTER DEM 而不是 FIT 的 altitude 字段。

**判断标准：**
| 骑行距离 | Strava 爬升正常范围 | 偏高警告 |
|---------|-----------------|---------|
| 145 km | 1500-2500 m | >3000 m |
| 106 km | 800-1500 m | >2000 m |
| 53 km | 300-700 m | >1000 m |

**变通方法**：在 Strava 网页端手动编辑（`Edit → Adjust Elevation`），或关闭「使用 DEM 校正高度」选项。

## QQ 附件缓存位置（关键！）

QQ 发来的 FIT 文件**不**在 strava-uploads 目录，而是缓存在：
```
~/.hermes/cache/documents/doc_<uuid>_qqdownloadftnv5
```

收到 [Attachment: xxx.fit] 后从这里复制到工作目录：
```bash
cp ~/.hermes/cache/documents/doc_<uuid>_qqdownloadftnv5 /home/agentuser/strava-uploads/xxx.fit
```

**注意：文件可能已存在**（用户重复发送/会话刷新），复制前用 `ls -la` 看时间戳确认。

然后用脚本上传：
```bash
python3 /home/agentuser/strava-upload.py /home/agentuser/strava-uploads/xxx.fit Ride
```

## 脚本路径

`/home/agentuser/strava-upload.py`

## Skill 打包导出工作流（2026-06-01 验证）

当用户要求"把这个 skill 打包发给我"或类似请求时：

**步骤**：
1. 复制整个 skill 目录到临时位置（避免污染原目录）
   ```bash
   rm -rf /tmp/skill-export/<name> && cp -r ~/.hermes/skills/<category>/<name> /tmp/skill-export/
   ```
2. 在导出目录根加 `README.md`（快速入门 + 目录树 + 关键文档阅读顺序）
3. `tar -czf` 打包（**不是 zip**——Linux server 默认没装 zip）：
   ```bash
   cd /tmp/skill-export && tar -czf <name>-skill-<date>.tar.gz <name>/
   ```
4. 用 `tar -tzf <archive>.tar.gz` 验证所有文件都在
5. 保存到 `~/strava-uploads/` 作为可投递位置

**坑**：
- ❌ `zip -r` → `zip: command not found`（服务器无 zip 包），用 tar.gz 替代
- ❌ base64 内嵌大文件到消息 → QQ/微信会被截断（>20KB 不可行）
- ❌ send_message 的 `target=qqbot` 配了 home channel 还报 "No home channel set"——疑似工具 bug，需重启 gateway
- ❌ send_message 工具的 email 平台**需要 `EMAIL_ADDRESS` 等环境变量**，但 hermes gateway 进程**没有**这些环境变量（只 config.yaml 顶层有，但 send_message 走 `os.getenv` 不读 config.yaml）

**文件发送回退链**（按优先级）：
1. send_message `target=qqbot` / `target=weixin`（限流时等 5-15 分钟）
2. 创建 cron job `deliver: email`，prompt 让 agent 把文件作为 MEDIA: 发出（**走 EmailAdapter，用 config.yaml 顶层 EMAIL_*，不需 os.getenv**）—— 实际可行的方案
3. 保存到 `~/strava-uploads/` + 文字告诉用户本地路径 + scp 提示

**Hermes 邮件双通道陷阱**：
- `send_message(target="email", ...)` → 用 `os.getenv("EMAIL_*")` → **hermes gateway 进程没这些 env var** → 失败
- `cronjob.create(deliver="email", ...)` → 走 `gateway.platforms.email.EmailAdapter` → 用 `config.yaml` 顶层 `EMAIL_*` 字段 → **可用**

后者是用户"每天能收到邮件"的真正机制。每日 06:00 的「博客调研」任务就是这种用法。

## 各次骑行 GPS 状态记录

| 日期 | 距离 | GPS 状态 | 处理方式 |
|------|------|---------|---------|
| 05-15 | 53 km | ✅ 干净（WGS-84，无漂移） | 直接上传 |
| 05-23 | 106 km | 严重漂移（坐标飞掉） | 需 Encoder 重建 |
| 05-26 | 53 km | 严重漂移（坐标飞掉） | 需 Encoder 重建 |
| 05-30 | 144 km | ⚠️ 两段式漂移（前79%偏东0.22°~0.48°，后段偏东0.14°）→ GPX 中介法修复 ✅ | GPX 中介 Encoder 重建，校正后 lon 108.57°~108.95° |
| 06-01 | 54 km | ✅ 干净（spread lat=0.062°, lon=0.088°，西安城区无漂移） | 直接上传 |

## fitdecode 正确用法（多次踩坑，已验证）

`fitdecode` 没有 `FitFile` 类，用 `FitReader`。fitdecode 的 FitDataMessage 对象**没有 `.get()` 方法、字段名是 `FitDataMessage` 的动态属性**，正确模式：

```python
import fitdecode

with fitdecode.FitReader('/path/to/file.fit') as fit:
    for chunk in fit:
        # 跳过 FitDefinitionMessage、FitHeader 等非数据消息
        if type(chunk).__name__ != 'FitDataMessage':
            continue
        # chunk.mesg_type.name 是消息类型字符串（'record', 'session', ...）
        if chunk.mesg_type.name != 'record':
            continue
        # ⚠️ 用 getattr 而非 dict comprehension：
        for f in chunk.fields:
            # f.name 字符串, f.value 原始值（semicircles int / datetime / float）
            v = getattr(chunk, f.name, None)  # 这样访问最稳
            # 或者：f.value 直接拿值
            print(f.name, f.value)
```

**关键陷阱（多次重试才意识到）：**
1. ❌ `chunk.get('position_lat')` → AttributeError, FitDataMessage 没有 .get()
2. ❌ `{f.name: f.value for f in chunk.fields}` → 看似对，但访问 field 自身属性会失败，必须用 `f.value` 而非 `f`
3. ❌ `chunk.timestamp` → AttributeError, 因为 chunk.fields 是 `FieldData` 对象，不是直接属性
4. ✅ 正确：`getattr(chunk, f.name, None)` 或 `f.value`（在循环里用 f.value 最简洁）

**距离字段 `distance`**：单位是**米**，不是公里！`max(r['distance'])` 直接除以 `1000` 得 km。

**坐标转换（semicircles → degrees）**：
```python
def semicircles_to_deg(s):
    return s / (2**32 / 360)
# 简化：s / (2**31/180) 也行（精度够用）
```

## 收到 FIT 附件后的工作流程（06-01 更新：默认不做偏移诊断）

**06-01 起新工作流（用户偏好）：** 用户说"不需要再管偏移了" → 默认直接上传，不再做 spread/聚类/异常点分析。

1. **QQ 附件找到**：`cp ~/.hermes/cache/documents/doc_<uuid>_qqdownloadftnv5 /home/agentuser/strava-uploads/xxx.fit`
2. **直接上传**：`python3 /home/agentuser/strava-upload.py /home/agentuser/strava-uploads/xxx.fit Ride`
3. **抓取活动详情报告**（距离/时间/爬升/均速/起点终点）

**仅当用户明确说"路线不对"或"地图偏移"时**，才进入诊断流程（见下「旧的 GPS 诊断工作流」）。

### 旧的 GPS 诊断工作流（仅供用户主动要求时使用）

```python
from fitdecode.reader import FitReader

with FitReader('/path/to/file.fit') as fit:
    records = []
    for block in fit:
        if type(block).__name__ == 'FitDataMessage' and block.mesg_type.name == 'record':
            r = {f.name: f.value for f in block.fields}
            records.append(r)

lons = [r['position_long'] for r in records if 'position_long' in r]
lats = [r['position_lat'] for r in records if 'position_lat' in r]
distances = [r.get('distance', 0) for r in records]

total_dist_km = max(distances) / 1000
def semicircles_to_deg(s):
    return s / (2**32 / 360)

print(f'距离: {total_dist_km:.1f} km')
print(f'Lon: {semicircles_to_deg(min(lons)):.4f} ~ {semicircles_to_deg(max(lons)):.4f}')
print(f'Lat: {semicircles_to_deg(min(lats)):.4f} ~ {semicircles_to_deg(max(lats)):.4f}')

timestamps = [r['timestamp'] for r in records if 'timestamp' in r]
if timestamps:
    dur_h = (timestamps[-1] - timestamps[0]).total_seconds() / 3600
    print(f'时长: {dur_h:.2f} h, 均速: {total_dist_km/dur_h:.1f} km/h')
```

- 坐标正常（34°N 附近，lon ≈ 108.8°）→ 直接上传
- 坐标漂移（lat > 50° 或 lon > 150°）→ 提示用户去 OnelapFit 更新星历
- 坐标正常但 lon 有两个聚类（差值 0.1-0.5°，无过渡）→ 两段式漂移，用 `scripts/correct_gps.py` 修复后上传

### GPS 正常的根本原因（星历同步）

骑前在 **OnelapFit（顽鹿运动）App** 连接码表**同步了最新星历**，GPS 全程没有失锁。每次骑车前都需执行此操作（星历有效期约 2-4 小时）。

## 参考资料

- `references/check_fit_gps.md` — GPS 质量快速检查脚本（上传前必跑）
- `references/strava-upload-status-check.md` — Strava API 上传状态查询、重复检测、活动 ID 反查方法
- `references/fit-rebuild-encoder.md` — fitdecode + garmin-fit-sdk Encoder 重建干净 FIT 文件的完整方案（GPS 漂移修复核心参考）。**重点：必须用 Decoder 的 mesg_listener 把所有消息原样透传给 Encoder，只修改 record 坐标；否则 Strava 报 "There was an error processing your activity"（元数据丢失）**
- `references/gpx-intermediary-method.md` — ✅ **推荐方案（2026-05-30 验证通过）**：Encoder 写入 metadata 失败时的 GPX 中介法。三段校正 + fitdecode→GPX→Encoder→FIT 全流程，已在 05-30 案例成功上传。
- `references/osm-snap-to-road-analysis.md` — **OSM 路网偏差分析（2026-06-01 新增）**：用 Overpass API + Shapely 给每个 GPS 点找最近路网，区分「GPS 真偏」vs「路网盲区」/「走了 OSM 没覆盖的小路」。5min 滑动窗是核心判断工具。
- `references/two-segment-gps-drift.md` — 两段式 GPS 漂移识别：spread 对比判断「两段式漂移」vs「路线本身更远」，附 `scripts/correct_gps.py` 直接可用
- ~~`references/magene-c706-gps-drift.md`~~ — **已删除（2026-06-01 归档）**：原文档基于错误的 "MAGENE C706 输出 WGS-84" 假设，整篇结论与新发现矛盾。被 `references/magene-c706-gcj02-offset.md` 完整覆盖。
- `references/magene-c706-gcj02-offset.md` — GCJ-02 系统偏移识别（**2026-06-01 推翻重写**：MAGENE C706 实际输出 GCJ-02，路线恒定方向偏 470-490m；OSM 双向验证是最可靠的诊断方法）
- `scripts/detect_gcj02_offset.py` — **🆕 GCJ-02 偏移一键诊断（2026-06-01）**：OSM 路网双向投影（WGS-84 vs GCJ-02），输出中位偏差对比。5-10 分钟给出明确结论。用法：`python3 scripts/detect_gcj02_offset.py <file.fit>`
- `references/strava-gcj02-wgs84-correction.md` — ✅ **新增（2026-05-31 验证）**：复刻 [xqdoo00o/strava_auto](https://github.com/xqdoo00o/strava_auto) 的 GCJ-02→WGS-84 迭代纠偏算法（outOfChina 边界 + _transform + _delta + gcj2wgs），以及 FIT 二进制坐标 patch（Record/Lap/Session/CoursePoint/SegmentPoint）、CRC-16 重建、gzip 压缩上传完整实现
- `references/fit-method-comparison.md` — ✅ **新增（2026-06-01）**：三种 FIT 修复方法（binary patch / fit-tool update / GPX 中介法）的决策树。给出偏移类型→方法映射、各方法优缺点、决策流程。**遇到 GPS 偏移问题先看这个，再选具体 reference**
