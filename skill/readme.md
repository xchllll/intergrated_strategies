# 技能包 (Skill Package)

本目录包含团队在开发“便携式电子电路智能诊断系统”过程中，沉淀下来的可复用经验与脚本。这些经验不仅适用于本项目，也可直接迁移至其他 PYNQ-Z2 或 Vivado 仿真工程中。

## 技能一：Vivado 波形显示排错指南
- **适用场景**：在 Vivado 中跑仿真时，数字信号（如正弦波、PID输出）波形显示异常（如折叠、全是方块）。
- **使用方法**：
  1. 若输出为负数（有符号数据），右键信号名 -> `Radix` -> 选择 `Signed Decimal`。
  2. 若要观察模拟包络线，右键信号名 -> `Waveform Style` -> 选择 `Analog`。
- **失效条件**：此方法仅改变仿真视图的显示方式，不改变底层硬件数据。若修改后波形仍杂乱，需排查 RTL 代码中是否发生了位宽溢出。

## 技能二：Windows 环境下 Git 权限与代理一键配置
- **适用场景**：使用 VS Code 或命令行推送代码到 GitHub 时，频繁提示 `not found`、`Permission denied` 或网络超时。
- **踩坑清单**：
  1. **网络超时**：Git 默认不走系统代理。需在终端执行 `git config --global http.proxy http://127.0.0.1:<你的加速器端口>`。
  2. **权限不足**：去 Windows 的“凭据管理器” -> “Windows 凭据”中，删除所有以 `git:https://github.com` 开头的记录，强制 Git 重新弹窗授权。
- **验证方法**：在终端运行 `git push`，看到 `Everything up-to-date` 或进度条满即可。

## 技能三：Python/Vivado 多环境与字符编码排雷指南
- **适用场景**：使用 VS Code 编写和运行 Python 脚本，或在 Vivado 中跑 Testbench 仿真时，遇到依赖库找不到、标点报错、乱码或死循环。
- **使用方法 / 踩坑清单**：
  1. **Python 模块找不到**：若 `pip install` 成功但 VS Code 报 `ModuleNotFoundError`，说明遇到了多环境冲突。在终端运行 `python -m pip install <模块名>` 强制给当前运行的 Python 安装。
  2. **代码带框/报错**：AI 生成的代码常带有中文全角逗号或不可见字符，VS Code 会高亮提示。复制后务必检查并替换为英文半角符号。
  3. **Vivado 仿真乱码**：Verilog 的 `$display` 尽量不要输出中文，防止 UTF-8 与系统 GBK 编码冲突。
  4. **Vivado 仿真死循环**：若修改了代码加了 `$finish` 仍不停，且提示 `Invalid Top Module`，说明新代码编译失败，Vivado 加载了旧快照。使用 `Flow -> Reset Behavioral Simulation` 清理缓存后重跑。

## 技能四：正交解调（I/Q Demodulation）定点化设计规范
- **适用场景**：在 FPGA 内进行交流信号（如正弦波）的幅值与相位测量，且仅有 16 位 ADC 采样精度时。
- **使用方法**：
  1. **混频（乘法）**：16位 ADC 信号与 16位本振（DDS生成的cos/sin）相乘，结果需用 32 位寄存器存储，防止溢出。
  2. **积分累加**：积分窗口为 N 个点时，需预估最大值。例如 15000 * 32767 * 1000 ≈ 4.9e11，需使用 **48位** 累加器（`reg signed [47:0]`）。
  3. **归一化**：计算最终幅度前，需将累加结果除以（N * 放大倍数）还原真实值。
- **失效条件**：若累加器位宽不足 48 位，长时间积分会导致数据高位溢出翻转，波形与计算完全错误。