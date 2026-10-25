# 最低档验收入口

目标：证明 HP+MIC 到 LINE IN 的物理回环真实存在，软件能测到非零 I/Q，
网页能读取同一份真实数据；不把演示 CSV 冒充硬件实测，也不把空校准表
宣传成“已校准”。

## 1. 接线

1. 用 3.5 mm 音频线短接 PYNQ-Z2 的 `HP+MIC` 与 `LINE IN`。
2. 板端 Jupyter 地址是 `http://192.168.2.99:9090`，默认密码 `xilinx`。
3. 网页仪表盘是独立 Flask 服务：`http://192.168.2.99:5000`。

## 2. 单点 I/Q 验收

板端执行：

```bash
cd /home/xilinx/jupyter_notebooks/project
python3 acceptance.py --backend audio_loopback --freq 1000 --repeats 3
```

这条路径使用 PYNQ 官方 `BaseOverlay + AudioADAU1761`，不依赖尚未重建的
自定义 bitstream。脚本会同时完成播放、录音、24 位有符号解码和相干 I/Q 计算。

验收必须全部满足：

- 三次采集中至少两次是有效频点；
- 实测频率偏离 1 kHz 不超过 30 Hz；
- 频谱峰值与底噪比至少 50；
- RMS 大于 50,000 code；
- 三次幅值离散度不超过 35%；
- I/Q 不同时为零。

通过时脚本退出码为 `0`，并在 `data/acceptance_latest.json` 保存完整证据。

## 3. 保留的 FPGA RTL 路径

自定义 bitstream 重建完成后，可以继续验证原来的 AXI I/Q 数据通路：

```bash
python3 acceptance.py --backend rtl --freq 1000 --periods 10
```

默认 `--backend auto` 在 PYNQ Linux 上使用官方音频回环；Windows 上使用 RTL
资产检查。

## 4. 网页仪表盘

板端后台启动：

```bash
cd /home/xilinx/jupyter_notebooks/project
nohup /usr/local/share/pynq-venv/bin/python3 \
  -m diagnosis_system.dashboard.app \
  > data/dashboard.log 2>&1 &
```

打开 `http://192.168.2.99:5000`，点击“开始扫频测量”。PYNQ 模式当前执行
12 个对数频点的官方音频闭环实测；校准表为空时会明确显示“未校准”。

网页 JSON 单点 I/Q 验收入口：

```text
http://192.168.2.99:5000/api/iq?freq=1000&repeats=3
```

## 5. 硬件引脚依据

PYNQ-Z2 官方资料包和 XDC 给出的音频方向是：

- `F17`：FPGA → codec，放音数据；
- `G18`：codec → FPGA，录音数据；
- `U5`：10 MHz codec MCLK；
- `R18`：BCLK；
- `T17`：LRCLK。
