# Topology-aware H 平面表（2026-11-02）

## 1. 表结构

`data/input_network.json` 现在是 **22 行 topology-aware 表**：

```text
meta.table_kind = "topology_aware"
16 行 series 标准件
6 行 shunt 标准件
R_ref = 10989.125 Ω
```

每行在 `meta.standards` 中包含 `topology`、`R_ohm`、`L_h`、`C_f` 和 `sweep`。默认 series 去嵌入只使用 16 行 series 子表；shunt 行通过 topology-aware 接口单独查询。

## 2. shunt 标准件

| 标准件 | topology | 参数 | sweep |
|---|---|---|---|
| `rc_lp_1k98n1` | `rc_lowpass` | 1 kΩ + 98.11 nF | `20261029-075359-209478` |
| `rl_hp_1k100m` | `rl_highpass` | 1 kΩ + 100 mH | `20261029-082449-112104`（补做） |
| `rc_lp_3k2_9n56` | `rc_lowpass` | 3.2 kΩ + 9.56 nF | `20261029-075937-558912` |
| `rc_lp_10k_1n` | `rc_lowpass` | 10 kΩ + 1 nF | `20261029-081720-588200` |
| `rl_hp_2k2_63m` | `rl_highpass` | 2.2 kΩ + 63 mH | `20261029-081427-311763` |
| `rl_hp_4k7_43m` | `rl_highpass` | 4.7 kΩ + 43 mH | `20261029-082219-202819` |

其中：

```text
rc_lowpass  : R 串联，C 并联到地
rl_highpass : R 串联，L 并联到地
```

`67 mH` 实测为 `63 mH`，`47 mH` 实测为 `43 mH`。`rl_hp_1k100m` 用补做的 `112104` 替换了旧的 `125400`。

## 3. 分组校验

### series 行

沿用原来的 series 校验路径，16 行结果与旧 series 表一致。

### shunt 行

shunt 行按 topology group 单独做 Zin 反解校验：

| 标准件 | Zin dB RMS / max | Zin 相位 RMS / max |
|---|---:|---:|
| `rc_lp_1k98n1` | 0.003 / 0.020 dB | 0.028° / 0.128° |
| `rl_hp_1k100m` | 0.001 / 0.006 dB | 0.125° / 0.474° |
| `rc_lp_3k2_9n56` | 0.003 / 0.020 dB | 0.016° / 0.103° |
| `rc_lp_10k_1n` | 0.001 / 0.007 dB | 0.001° / 0.005° |
| `rl_hp_2k2_63m` | 0.009 / 0.030 dB | 0.235° / 0.835° |
| `rl_hp_4k7_43m` | 0.006 / 0.023 dB | 0.246° / 0.897° |

22 行整体 in-sample：

```text
dB RMS 均值    0.0096 dB
dB RMS 最大    0.0699 dB
相位 RMS 均值  0.0652°
相位 RMS 最大  0.2458°
```

## 4. 运行时行为

`python_model/input_network.py` 读取：

```text
meta.table_kind
meta.standards[*].topology
```

网页左上角新增 **DUT 接线方式**：

```text
二端口 series  → deembed_complex()，只取 16 行 series 子表
三端口 shunt   → deembed_shunt_equivalent()，只取 6 行 shunt 子表
```

`deembed_shunt_equivalent()` 是明确的“等效 series 显示”：使用 shunt 子表估计 Zin，再按 series 等效阻抗重嵌入 `R_ref`。三端口网络的严格 `H_disp` 仍需要 DUT 的 R/L/C 参数；网页会在 warnings 中提示。

- `deembed_complex()` 默认使用 `topology="series"`；
- `zin_at(..., topology="shunt")` 或具体 topology 名使用 shunt 子表；
- 对 shunt-only 表或显式请求 shunt 的 `deembed_complex()` 会明确报错，不静默套用 series 公式；
- 默认 series 去嵌入结果与旧 16 行 series 表对比，最大差异为 **0**。

## 5. 限制

- 网页已有 series/shunt 接线方式选择，但还没有 shunt DUT 的 R/L/C 输入，因此 shunt 模式是等效 series 显示，不是严格三端口反解。
- shunt 行是实测数据，低频异常/夹具残余会保留在表中，不会被理论模型自动“修正”。
- in-sample 校验只证明建表/分组链路自洽，不是独立测试。

## 6. 产物

- H 平面表：[`data/input_network.json`](../data/input_network.json)
- 标准件清单：[`tools/hplane_standards.20261102.json`](../tools/hplane_standards.20261102.json)
- 短接表：`data/calibration_table.json`
- 建表工具：[`tools/build_hplane.py`](../tools/build_hplane.py)
- 运行时加载：[`python_model/input_network.py`](../python_model/input_network.py)
