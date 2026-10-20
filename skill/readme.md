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