# 最低档验收手册

目标：先证明“软件能控制 FPGA、能读出真实 I/Q、网页能把数据画出来”，
不把演示 CSV 冒充硬件实测，也不把空校准表宣传成“已校准”。

## 1. 上板前接线

1. 用 3.5 mm 音频线短接 PYNQ-Z2 的 `HP+MIC` 与 `LINE IN`。
2. 板子使用与 `board/design_1_wrapper.xsa` 对应的 PYNQ 镜像启动。
3. SSH 进入板子，仓库位于 `D:\.integrated_strategies` 对应的板端目录。

当前交付 XSA 的 `FCLK_CLK0` 是 **50 MHz**。软件默认值已改为 50 MHz；
如果换用了别的 bitstream，必须在运行前显式设置：

```bash
export PYNQ_DEMOD_CLK_HZ=50000000
```

## 2. 单点 I/Q 自检（最先跑）

```bash
python board/acceptance.py --freq 1000 --periods 10
```

脚本会依次：

1. 提取或确认 `board/design_1_wrapper.bit`。
2. 用官方 `BaseOverlay + Audio` 初始化 ADAU1761 codec 并选择 LINE IN。
3. 加载项目自己的 bitstream。
4. 启动 1 kHz 单点测量，轮询 `out_done`，读取 48 位 `I/Q`。
5. 在屏幕和 `data/acceptance_latest.json` 中保存原始十六进制、十进制、
   派生电压/相位、窗长、时钟和校准状态。

判据：

- 必须出现 `"done": true`。
- `acc_i`、`acc_q` 至少要有一个非零。
- 出现全零时脚本返回退出码 3，并明确提示检查 codec、LINE IN 和短接线。
- 若一直等不到 `out_done`，脚本报超时，不返回上一次的陈旧缓存值。

脚本还会报告由 FPGA 时钟派生的 I2S 采样率。当前 50 MHz 下约为
**24.4 kHz**，与官方 codec 常见的 48 kHz 配置不一致；这是一项真实风险，
短接后若 `I/Q` 异常，应优先检查这条时钟链，不能把异常数据判成成功。

## 3. Windows 网页演示

在 Windows 仓库根目录：

```powershell
diagnosis_system\.venv\Scripts\python.exe diagnosis_system\dashboard\app.py
```

然后浏览器访问 `http://127.0.0.1:5000`。Windows 模式固定读取
`data/dataset_rc_lpf.csv`，页面会明确标为“Windows 演示数据”，并显示
“演示 CSV 不是 PYNQ 硬件测量结果”。

## 4. 板端网页真实扫频

codec 初始化成功后，在同一个 Python 会话或已配置好的板端环境启动网页，
点击“开始扫频测量”。板端模式会读取真实 `I/Q`，并在响应里携带：

- `source.is_real = true`
- 校准表状态
- 未校准时明确警告
- 50 MHz / 48 位累加器条件下，低频扫频自动从约 239 Hz 开始并说明原因

最低档验收只要求真实 `I/Q` 非零；不要求机器学习结果、参数反推或四态校准
在本轮全部完成。
