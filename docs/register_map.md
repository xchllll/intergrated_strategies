# AXI GPIO 寄存器地址映射表

| 硬件模块 | 基地址 (Base Address) | 通道 1 (GPIO) 功能 | 通道 2 (GPIO2) 功能 |
| :--- | :--- | :--- | :--- |
| `axi_gpio_0` | `0x4120_0000` | 写 32 位 `ps_phase_inc` (频率控制字) | 写 1 位 `ps_start` (启动信号) |
| `axi_gpio_1` | `0x4121_0000` | 写 32 位 `ps_window_max` (积分窗长) | 读 1 位 `out_done` (测量完成标志) |
| `axi_gpio_2` | `0x4122_0000` | 读 32 位 `out_acc_I_low` (I 路低 32 位) | 读 16 位 `out_acc_I_high` (I 路高 16 位) |
| `axi_gpio_3` | `0x4123_0000` | 读 32 位 `out_acc_Q_low` (Q 路低 32 位) | 读 16 位 `out_acc_Q_high` (Q 路高 16 位) |