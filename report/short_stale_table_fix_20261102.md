# 重启后短接 20 kHz 相位 130° 的修复与验证（2026-11-02）

## 1. 现象与根因

用户反馈：重启进程后，短接扫频在 20 kHz 的校准相位仍偏离约 130°。`data/history.csv` 最新一轮：

```text
sweep_id = 20261028-231233-059108
20 kHz: raw 85.443 dB / +86.054°  ->  cal 2.518 dB / −130.805°
```

检查发现：

- dashboard 于 23:11 重启，但当前进程的 `reference.done=false`；
- 最新扫频 23:12:33 使用的仍是上一次进程 23:04:29 生成的 `data/calibration_table.json`（`align3`，20 kHz `phase_offset=+143.14°`）；
- 计算：`raw +86.05° + table +143.14° = +229.19° ≡ −130.81°`。

所以这次 **不是新的跨进程帧偏移**，而是**重启后没有做当前进程的参考扫频，直接用了旧 session 的短接表**。旧表与新进程的短接相位差约 229°，在 20 kHz 就表现为 −130°。

## 2. 软件修改

### 2.1 启动后自动参考扫频

`run_dashboard.py` 在 `_ensure_rtl_design()` 后自动启动一轮 240 点短接参考扫频：

```text
[startup] auto short-reference sweep started; wait for phase alignment ...
[startup] dashboard ready; sweep is blocked until reference is done
```

参考完成前，`/api/start_sweep` 返回 409，不会用旧表写 `history.csv`。

### 2.2 校准表增加 session_id

`app.py` 为每个 dashboard 进程生成 `SESSION_ID = uuid.uuid4().hex`：

- `startup_reference.run(..., session_id=SESSION_ID)` 把 `session_id` 写进 `calibration_table.json` 的 `meta`；
- `_run_rtl_hardware_sweep()` 校验校准表 `meta.session_id == SESSION_ID`，不一致直接报错；
- `/api/status` 返回当前 `session_id`，便于核对；
- 手动参考（网页按钮）也使用当前 session ID。

### 2.3 参考未完成时拒绝扫频

`/api/start_sweep` 在 `pynq_rtl` 模式下检查参考状态：

```text
running  -> 409 正在进行相位对齐
done=false -> 409 参考扫频尚未完成/失败
```

这样即使页面加载后立即点“开始扫频测量”，也不会把旧 session 的 −130° 写进历史。

## 3. 三次重启 + 自动参考 + 短接验证

每次重启后等待自动参考完成，再对**短接**执行一次完整网页扫频，读取 `history.csv` 最新一轮。

| 轮次 | 参考 session | 20 kHz 原始相位 | 20 kHz 校准后 | 1 kHz 校准后 |
|---|---|---:|---:|---:|
| 1 | `91aacc7b…` | −61.390° | **0.000 dB / +0.0035°** | 0.0002 dB / +0.0034° |
| 2 | `eba66485…` | −61.398° | **−0.0002 dB / −0.0023°** | −0.0002 dB / +0.0005° |
| 3 | `c46a62dd…` | −61.395° | **+0.0004 dB / +0.0037°** | 0.0002 dB / +0.0023° |

三次 20 kHz 校准相位都落在 **±0.004°** 内，130° 偏移消失；1 kHz 也在 ±0.004° 内。

对应最新 history：

```text
cycle 1: 20261028-232127-791498
cycle 2: 20261028-232310-341280
cycle 3: 20261028-232518-576179
```

## 4. 结论

- 本次现象的直接原因是**旧短接表被新进程复用**，不是新的帧偏移；
- 软件层已通过 **自动参考 + session_id 校验 + 参考未完成禁止扫频** 彻底堵住这条路径；
- 当前线上 `calibration_table.json` 是第 3 轮自动参考生成的表（created `2026-10-28T23:24:17`，session `c46a62dd…`），dashboard 保持运行；
- `data/input_network.json` 未改。

> 注意：这解决的是“重启后没参考/用了旧表”的 130°。跨 session 的 Codec 帧相位本身仍存在；但自动参考会让当前进程的短接与 DUT 使用同一张表，正常 live 测量会抵消。若要做跨 session 的旧标准件回放，继续使用 `tools/short_compare.py` 的离线对齐。
