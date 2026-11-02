# Topology-aware H 平面表（2026-11-02）

## 1. 表结构

`data/input_network.json` 现在是 **19 行 topology-aware 表**：

```text
meta.table_kind = "topology_aware"
16 行 series 标准件
3 行 shunt 标准件
R_ref = 10989.125 Ω
```

每行在 `meta.standards` 中带有 `topology`、`R_ohm`、`L_h`、`C_f` 和 `sweep`。series 与 shunt 行不再混在同一个 H→Zin 插值空间里。

## 2. 新增 shunt 标准件

| 标准件 | topology | 参数 | sweep |
|---|---|---|---|
| `rc_lp_1k98n1` | `rc_lowpass` | 1 kΩ + 98.11 nF | `20261029-075359-209478` |
| `rl_hp_1k100m` | `rl_highpass` | 1 kΩ + 100 mH | `20261029-075745-125400` |
| `rc_lp_3k2_9n56` | `rc_lowpass` | 3.2 kΩ + 9.56 nF | `20261029-075937-558912` |

其中：

```text
rc_lowpass  : R 串联，C 并联到地
rl_highpass : R 串联，L 并联到地
```

## 3. 分组校验

### series 行

沿用原来的 series 校验路径，16 行 series 行结果与 16 行表一致。

### shunt 行

shunt 表没有统一的 series `Z_dut`，校验对象改为 **H 平面插值出的 Zin**：

| 标准件 | Zin dB RMS / max | Zin 相位 RMS / max |
|---|---:|---:|
| `rl_hp_1k100m` | **0.002 / 0.005 dB** | **0.048° / 0.182°** |
| `rc_lp_3k2_9n56` | **0.000 / 0.001 dB** | **0.000° / 0.001°** |
| `rc_lp_1k98n1` | **0.001 / 0.006 dB** | **0.007° / 0.022°** |

19 行整体 in-sample：

```text
dB RMS 均值    0.0100 dB
dB RMS 最大    0.0699 dB
相位 RMS 均值  0.0441°
相位 RMS 最大  0.1768°
```

## 4. 运行时行为

`python_model/input_network.py` 现在会读取：

```text
meta.table_kind
meta.standards[*].topology
```

`deembed_complex()` 默认使用 **series 子表**：

```python
input_network.deembed_complex(h, freq)
# 等价于 topology="series"
```

因此：

- 默认 dashboard 的 series 去嵌入只使用 16 行 series 标准件；
- 3 行 shunt 标准件不会污染 series DUT 的 H→Zin 插值；
- 与原 16 行 series 表对比，默认 `deembed_complex()` 的结果最大差异为 **0**（对同一组测试点）。

需要检查 shunt 子表时可以显式指定：

```python
input_network.zin_at(freq, h, topology="shunt")
input_network.zin_at(freq, h, topology="rc_lowpass")
input_network.zin_at(freq, h, topology="rl_highpass")
```

并将 `table_kind()` 返回 `"topology_aware"`。

## 5. 限制

- 当前 dashboard 没有“未知 DUT 的 topology”输入，因此默认仍按 series 显示；shunt 行已经进入表，但还没有接到 UI 的 shunt 显示路径。
- `rl_hp_1k100m` 的低频 H 和 1 kΩ + 100 mH 理论值不完全一致；如果实际电阻不是 1 kΩ，需要用实测值重建。
- in-sample 校验只证明建表/分组链路自洽，不是独立测试。

## 6. 产物

- H 平面表：[`data/input_network.json`](../data/input_network.json)
- 标准件清单：[`tools/hplane_standards.20261102.json`](../tools/hplane_standards.20261102.json)
- 短接表：`data/calibration_table.json`
- 建表工具：[`tools/build_hplane.py`](../tools/build_hplane.py)
- 运行时加载：[`python_model/input_network.py`](../python_model/input_network.py)
