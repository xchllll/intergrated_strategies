# 性能指标汇总

## 1. 采集与扫频

- 240 点对数扫频，20 Hz–20 kHz；`fs = 100 MHz / 2048 = 48828.125 Hz`；单点积分窗 `N = periods · fs / f`。
- 单轮 240 点约 **20 s**（短接参考，`n_target=1000`，周期 10–410）。
- 1 kHz 单点：`periods=10`、`window_n=488`、理论积分 9.994 ms、实测耗时 12.1 ms（见 `data/acceptance_latest.json`）。

## 2. 测量重复性

- 短接扫频：20 kHz **0.00 dB / 0.00°**；全频段相位 −0.02…+0.07°。
- 短接参考 240 点：20 kHz 增益 `5.347e-5`、相位 `−86.12°`，与建表短接 `5.348e-5 / −86.06°` 一致。

## 3. 校准精度

- 9.8 mH + 11.6 kΩ：20 kHz **−6.28 dB / −3.14°**，模型 −6.28 dB / −3.12°。
- 14 行 H 平面误差：幅度 rms 0.07–0.55 dB（max 1.91 dB），相位 rms 0.69–4.14°（max 10.04°），逐行见 `report/hplane_14rows_errors.md`。
- 正交解调归一化：`norm_factor = N · ADC_GAIN · LO_GAIN`；1 kHz 原始幅值约 0.00518 V，相位 133.69°（`acceptance_latest.json`）。

## 4. 分类（实测数据）

- 数据集：5 类各 30 条，共 **150 条**（`data/real/`），按类别分层抽 20% 作测试集。
- 决策树（DT）**100.00%**；MLP **100.00%**（`report/ml_training_note.md`、`report/confusion_matrix.png`）。
- 说明：训练/测试均来自真实硬件采集，未做额外域外测试；该准确率不代表跨设备泛化能力。

## 5. 上板验收

- `data/acceptance_latest.json`：backend=rtl，1 kHz，`done=true`，PL 时钟 100 MHz，`passed=true`。
- `data/acceptance_rtl_latest.json`：2025-05-04 的旧记录，`passed=false`（保留作历史对照，不作为当前结果）。
