# 大模型协作记录 (Large Model Collaboration Log) — P0 前置自检

上板之前先做一轮不依赖硬件的自检：PS 侧驱动单测 + 校准模块的 mock 闭环。记录保留原始输出，按其他协作记录的“表格 + 阶段总结”格式整理。

| 轮次 | 用户提示词 (Prompt) | 模型回答摘要 | 自我纠错 / 验证过程 |
| :--- | :--- | :--- | :--- |
| 1 | P0 前置自检：运行 `test_ps_ctrl.py` 与 `calibration.py`（mock 闭环，不依赖硬件），确认驱动与校准口径正确。 | `test_ps_ctrl.py`：`....` / `Ran 4 tests in 0.006s` / `OK`。`calibration.py`：`acc_I=1737733 acc_Q=1737733 (N=100) -> mag=1.5000 V, phase=45.00 deg`，与 `demod_sim.py` 一致；四态校准(1kHz) `direct=(1.0, 0.0), buffer=(1.0, 0.0)`；直连/缓冲校准后均为 `mag=1.5000, phase=45.00`；闭环自检（`mock=True`）在 500/1000/2000 Hz 两路 `gain=1.0000 off=0.00°`；`calibrated=True, 频点数=3, 范围=500~2000 Hz, 路径={'direct': 3, 'buffer': 3}`；重新加载表后 `apply_calibration -> mag=1.5000, phase=45.00`。 | **验证**：4 项单测全过；归一化幅值/相位与 `demod_sim.py` 单点一致；四态校准两路径增益/相位符合期望；校准表写入、状态查询、重新加载、`apply_calibration` 回代全部通过。该轮不依赖硬件，作为上板前的前置自检。 |

## 阶段总结

P0 自检把“上板后才发现”的问题尽量前移：单测覆盖驱动的寄存器读写与触发时序，mock 闭环覆盖四态校准的增益/相位、表写入与热加载。只要这一轮全绿，上板后就只需关注 PLL、I2C、I2S 等真实链路问题，而不用再回头怀疑定标口径。
