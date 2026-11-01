# 运行环境与版本

## 板端（PYNQ-Z2）

| 项 | 值 |
|---|---|
| 系统 | PYNQ Linux 3.0 (Carlisle) |
| Python | 3.10.4 |
| pynq | 3.1.1 |
| numpy / scipy | 1.21.5 / 1.8.0 |
| matplotlib / Flask | 3.5.1 / 2.1.3 |
| XILINX_XRT | `/usr` |
| PL 时钟 | 100 MHz |
| 采样率 | `fs = 100 MHz / 2048 = 48828.125 Hz` |
| bitstream | `board/design_1_wrapper.bit`，sha256 `35eacf4b4a43566b0e3d2e88c137a22ecfb25741ab102dd58291eca193cc26dd` |
| XSA | `board/design_1_wrapper.xsa`，sha256 `60a9e01d6188b0bc7a43d7f40b4f4d8049adb5f1d8c0c02db0600c0ccadefc81` |

## PC / Vivado

- Vivado 工程由 `build/build.tcl` 生成；综合/实现报告截图在 `report/`（`resource_utilization.png`、`timing_summary.png`）。
- 原始 `.rpt`（utilization/timing）和 Vivado 版本号待补；建议在导出报告时一并记录 Vivado 版本与日期。

## 复现步骤

```bash
# 1) 板端启动 dashboard（自动加载 codec/PL）
cd /home/xilinx/jupyter_notebooks/project
bash start_dashboard.sh
# 浏览器打开 http://<板子IP>:5000

# 2) 接短接，网页右下角点“参考扫频”（或 bash start_dashboard_ref.sh）

# 3) 重新标定（可选）
python3 board/calibrate_path.py

# 4) 采 DUT 数据
python3 tools/collect_dut.py
```

## 校准相关文件的可变性

`data/calibration_table.json`、`data/input_network.json` 和 `report/hplane_14rows_errors.md` 中的误差，都依赖**当次测量环境**（短接帧相位、线缆、标准件容差、温度）。换环境后数值会变，但可用同一套流程重新得到：短接参考 → 标准件原始扫频 → 重建 H 平面表 → `check_hplane.py` 复核。仓库保留的是某次可用环境下的快照，用于复现和对照，不是固定真值。
