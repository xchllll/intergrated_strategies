# Universal PYNQ Skill

> 面向 PYNQ 框架的通用技能，不绑定本项目；换一个项目或另一块 PYNQ 板卡也可以直接复用。
> 来源：`skill/README.md` 的分类整合版。含“Overlay 加载/校验、寄存器映射、register_map 使用范式”要素的技能排在前面，其余按可复用性从高到低排序。

## 一、优先：Overlay / 寄存器映射 / register_map 相关

### 1. Zynq-7000 PS+PL (AXI GPIO) 软硬件联调排雷指南

- **适用场景**：使用 PYNQ 框架，通过 Python（PS侧）控制 PL 侧自定义 IP（RTL代码）的数据读写时。
- **使用方法 / 踩坑清单**：
  1. **外部复位陷阱**：如果在 `top.v` 中使用了外部 `rst_n` 输入，但在 Block Design 中悬空，会导致芯片永远处于复位状态。**强烈建议**：在 RTL 内部写一个上电自动复位计数器（如数 255 个周期后拉高 `sys_rst_n`），彻底抛弃外部复位，避免物理按键未接导致的死锁。
  2. **位宽匹配**：48 位或 64 位的超大寄存器，不要试图在 BD 中直接连 32 位的 AXI GPIO。应在 RTL 顶层（`top.v`）直接拆分为高低位（如 Low 32 + High 16），在 BD 中分别接双通道 GPIO。
  3. **Python API 掩码**：PYNQ 新版本中，`AxiGPIO.channel.write(data, mask)` 必须带掩码，写 32 位全位时填 `0xFFFFFFFF`。
  4. **48位有符号数还原**：Python 读回高低位拼接后，`(high << 32) | low` 得到的是无符号数。必须判断是否大于 \(2^{47}\)，大于则减去 \(2^{48}\) 才能得到真实的负数（补码还原）。
- **失效条件**：上述规则适用于 Zynq-7000 系列与 PYNQ 3.x 及以上版本。若不按掩码写入，硬件寄存器将不会被修改。

### 2. AXI GPIO 硬件映射取证与 PS 侧驱动编写规范

- **适用场景**：在 PYNQ / Zynq 上编写 Python 驱动控制 PL 侧 AXI GPIO 外设时，需要确切的基地址、通道位宽与信号连接关系，不能靠猜。
- **使用方法 / 踩坑清单**：
  1. **不猜地址**：Vivado 导出的 `.xsa` 本质是 zip 包。解压后读取 `design_1.hwh`（IP 端口连接表）和 `design_1.bda`（地址段映射）。在 `bda` 中搜 `SEG_<IP>_Reg` 可找到 `C_BASEADDR`（如 `0x41200000`）。
  2. **寄存器偏移固定**：axi_gpio 数据寄存器偏移为 ch1=0x00、ch2=0x08，TRI 方向为 0x04/0x0C；先核对参数 `C_GPIO_WIDTH` / `C_GPIO2_WIDTH`，位宽不足时高 16 位会被截断。
  3. **48 位有符号回读**：累加器拆成“低 32 位 + 高 16 位”两个通道拼接，再按补码符号扩展成 Python 负数（`v - (1<<48) if v >= (1<<47)`）。
  4. **时序清零陷阱**：PS 把 `start` 拉低后，状态机回 IDLE 会触发 `clear` 清零累加器——取数必须在 start 仍为高电平时完成，驱动里用“启动 → 等待 → 读数 → 停止”的便捷接口规避。
- **失效条件**：直接按“常见 0x40000000”猜基地址，地址错误时 AXI 写静默失败；未核对通道位宽直接读 48 位，得到的是截断/错位数据。

### 3. Flask + PYNQ 的启动顺序——PL 必须在主线程加载

- **适用场景**：dashboard 后台线程加载 PL 报 `There is no current event loop in thread`；或网页起来后第一次扫频才加载 bitstream，导致第一次测量失败。
- **使用方法 / 设计规范**：
  1. **主线程加载**：`_ensure_rtl_design()`（`acceptance.configure_codec()` + `load_design(ensure_bitstream(), 1e8)`）在 `app.run()` 之前、主线程执行；后台线程只做扫频。
  2. **Flask 单线程**：`app.run(host="0.0.0.0", port=5000, debug=False, threaded=False)`，避免多请求并发争用 MMIO/PL。
  3. **启动即参考**：参考线程 daemon 化，主线程继续 `app.run()`；网页轮询 `/api/status` 拿 `reference.running/done/error`。
  4. **venv re-exec**：系统 python 没有 pynq 时自动 `os.execv` 到 pynq venv；`XILINX_XRT=/usr` 要在 import pynq 前设置。
  5. **脚本/服务路径**：`start_dashboard.sh` 用 `sudo env XILINX_XRT=/usr ... /usr/local/share/pynq-venv/bin/python3 run_dashboard.py`；直接 `python3 run_dashboard.py` 由 re-exec 兜底。
- **失效条件**：若多个进程同时加载 bitstream/争用 `/dev/mem`，会出现 `Resource busy`；需要先 `pkill` 旧 dashboard 再启动。


## 七、数据与机器学习

### 4. Python-PL 软硬联调的轮询时间规范

- **适用场景**：PS 侧通过 Python 操作 MMIO（如 AXI GPIO）控制 PL 状态机，并轮询 `done` 信号时。
- **核心设计规范**：
  1. **理论时间不可靠**：积分时长（如 `window_n / fs`）只是理论硬件执行时间。
  2. **开销补偿**：Python 的 `time.sleep()` 精度极差，单次 MMIO 读写也有几十微秒的延迟。`timeout` 必须给足裕量。例如，10ms 的硬件积分，超时阈值必须设为 50ms 甚至 100ms。
  3. **调试三板斧**：当 `measure_once` 超时时，不要先怀疑 RTL 代码。第一步用 `MMIO` 裸写寄存器拉高 `start`；第二步死等 50ms；第三步直接读取地址。如果裸测能读到 `done=1`，说明硬件没问题。
- **失效条件**：如果裸写 MMIO 且死等后 `done` 仍为 0，说明 RTL 状态机因缺少数据激励（如音频线未接）或时钟丢失而死锁。

### 5. 硬件状态位防冲突与强制复位

- **适用场景**：上位机控制 FPGA 时，需要在“自定义 RTL 扫频”和“官方音频闭环”两个模式间来回切换。
- **使用方法 / 踩坑清单**：
  1. **全局标志位**：引入 `_RTL_DESIGN_LOADED` 记录自定义 bitstream 是否已烧录，防止每次扫频都重烧。
  2. **冲突点**：调用官方音频驱动会加载 base.bit 重置 PL，但全局标志位仍为 `True`，切回 RTL 时会因寄存器不对而超时死机。
  3. **强制复位**：在初始化音频驱动（`force_reinit=True`）时，必须显式将 `_RTL_DESIGN_LOADED = False`。
- **验证方法**：RTL扫频 -> 切到Audio模式 -> 再切回 RTL 扫频，若无 `out_done 超时` 报错，则说明状态位复位成功。


## 五、测量与校准

## 二、其他通用 PYNQ 技能（按可复用性从高到低）

### 6. 板端部署与 Git 推送的取证流程

- **适用场景**：需要把 Python/JSON/XSA 部署到 PYNQ 板，并确认 GitHub 远端与本地一致。
- **使用方法 / 踩坑清单**：
  1. **文本与二进制分开传**：文本走 Jupyter contents API 的 `format=text`；XSA/二进制走 `format=base64`（`upload_bin.py`）。直接 `cat` 大文件会截断。
  2. **Jupyter 根目录不是 `/`**：`/tmp/xxx.json` 会 404；路径要写 `project/data/...`（相对 `/home/xilinx/jupyter_notebooks`）。
  3. **部署后校验 sha256**：本地/远端逐文件比对（`verify_board_files.py`），不要只看“上传成功”。
  4. **GitHub 推送**：网络不通时用 SSH 443 包装（`ssh_us.cmd`：`140.82.112.36:443` + `HostKeyAlias=ssh.github.com`）；`git push` 报 `Connection timed out` 可重试，成功后 `git ls-remote` 仍可能超时，以 push 输出为准。
  5. **GitHub API 抓取有 100 KB 截断**：`web_fetch` 的 raw/contents 大文件会被截断，校验大文件要靠本地 sha256 或分段抓取。
- **失效条件**：板端无外网/DNS 时只能走局域网 Jupyter；XSA 的 base64 上传对大小敏感，超大文件建议分块。

### 7. PYNQ 局域网共享与静态 IP 配置规范

- **适用场景**：PYNQ 板子直连电脑并通过电脑（如手机热点）共享网络，或通过路由器连接局域网，需要给板子配置固定 IP 以访问网页。
- **踩坑清单**：
  1. **Windows 共享（ICS）失效**：在电脑的“网络连接”里，把外网网卡（如 WLAN）属性切到“共享”，勾选“允许其他网络用户连接”并指向以太网卡。若没反应，去 `services.msc` 强制启动 `Internet Connection Sharing (ICS)` 服务。
  2. **网段冲突**：开启共享后，Windows 常会把电脑以太网卡 IP 强制变成 `192.168.137.1`。必须把板子 IP 设在同一网段（如 `192.168.137.99`），否则物理通但逻辑不通。
  3. **DNS 丢失**：Linux 板子配好静态 IP 后，务必手动写入 DNS，否则 `ping 8.8.8.8` 能通，但 `pip` 下载会报 `Temporary failure in name resolution`。
- **验证方法**：在板子终端 `ping -c 3 www.baidu.com` 若返回时间而非错误，且 `/etc/resolv.conf` 中写入了 `nameserver 8.8.8.8`，即为成功。
- **失效条件**：板子断电重启后，手动敲的 `ifconfig` 和 `route` 命令会失效，需重新配置或写入自启脚本。

### 8. 跨平台环境自适应与代码优雅降级

- **适用场景**：同一套上位机（网页+算法）代码，既要能在 Windows 电脑上无硬件演示（读 CSV），又要在 PYNQ 上跑真实硬件。
- **使用方法 / 踩坑清单**：
  1. **自动识别**：在 `app.py` 中引入 `platform.system()`。Windows 下自动读本地 CSV；Linux（PYNQ）下自动调 T1 的 `ps_ctrl` 等硬件模块。
  2. **防崩溃保护**：在使用 `import` 引入硬件驱动时，务必包一层 `try-except` 或基于环境判断，防止在电脑上因无 `pynq` 库直接崩溃红字。
- **验证方法**：在电脑上跑，网页显示“演示数据”；原样拷到板子跑，网页显示“实测数据”，一套代码两地开花。

### 9. Python 跨目录文件路径锚定法

- **适用场景**：运行脚本时（如 `python train.py`），无论终端在哪个目录下，脚本都找不到相对路径的 `data/` 或 `model/` 文件夹。
- **使用方法 / 踩坑清单**：
  1. **废弃相对路径**：绝对不要用 `../data/` 这种依赖当前终端位置的相对路径。
  2. **路径锚定**：使用 `pathlib` 获取当前文件的绝对路径并向上回溯，如 `ROOT_DIR = Path(__file__).resolve().parents[2]`，再拼上 `ROOT_DIR / "data" / "file.csv"`。
- **验证方法**：在任何一个嵌套目录下执行脚本，路径均不会报 `FileNotFoundError`。

### 10. VS Code 虚拟环境 (venv) 冲突与真伪验证

- **适用场景**：在 VS Code 中新建文件夹并配置 Python 环境时，系统提示“已将包安装到全局环境中”，或不确定依赖是否装入了虚拟环境。
- **使用方法 / 踩坑清单**：
  1. **忽略假警报**：若终端命令行前缀已出现绿色的 `(.venv)`，但 VS Code 右下角仍弹窗提示“建议创建虚拟环境”，直接点“不再显示”。这是 VS Code 的 Python 插件界面未及时同步导致的。
  2. **终极验证法**：在激活状态下，终端输入 `where pip`。如果第一条路径是 `...\.venv\Scripts\pip.exe`，说明包百分之百装在了隔离环境中。
  3. **选择解释器**：按 `Ctrl+Shift+P` 打开命令面板，输入 `Python: Select Interpreter`，手动指定路径包含 `.venv` 的那个 Python 解释器，可彻底消除警告。
- **失效条件**：若在虚拟环境中 `pip install` 成功，但运行代码依然报 `ModuleNotFoundError`，说明当前终端运行的并非 `.venv` 里的 Python，需检查并重新激活。

### 11. Git 强制走代理与网络超时（443端口）排错

- **适用场景**：在 Windows 环境下使用 VS Code 终端 `git push` 时，频繁报错 `Failed to connect to github.com port 443 after 21000 ms: Could not connect to server`。
- **踩坑清单**：
  1. **系统代理与 Git 脱节**：在 CMD 运行 `netsh winhttp show proxy` 显示“直接访问”，但这仅代表 Windows 系统服务没走代理。Git 默认是独立的，不会自动吃浏览器的代理。
  2. **找对代理端口**：打开代理软件（如 SakuraCat），找到“Mixed 端口”（如 `127.0.0.1:12450`），该端口通常兼容 HTTP 和 SOCKS5。
  3. **配置 Git 代理**：在终端执行：
     `git config --global http.proxy http://127.0.0.1:12450`
     `git config --global https.proxy http://127.0.0.1:12450`
- **验证方法**：执行 `git config --global --get http.proxy` 检查是否生效，再次 `push` 成功即说明解决。

### 12. Windows 环境下 Git 权限与代理一键配置

- **适用场景**：使用 VS Code 或命令行推送代码到 GitHub 时，频繁提示 `not found`、`Permission denied` 或网络超时。
- **踩坑清单**：
  1. **网络超时**：Git 默认不走系统代理。需在终端执行 `git config --global http.proxy http://127.0.0.1:<你的加速器端口>`。
  2. **权限不足**：去 Windows 的“凭据管理器” -> “Windows 凭据”中，删除所有以 `git:https://github.com` 开头的记录，强制 Git 重新弹窗授权。
- **验证方法**：在终端运行 `git push`，看到 `Everything up-to-date` 或进度条满即可。

### 13. Python/Vivado 多环境与字符编码排雷指南

- **适用场景**：使用 VS Code 编写和运行 Python 脚本，或在 Vivado 中跑 Testbench 仿真时，遇到依赖库找不到、标点报错、乱码或死循环。
- **使用方法 / 踩坑清单**：
  1. **Python 模块找不到**：若 `pip install` 成功但 VS Code 报 `ModuleNotFoundError`，说明遇到了多环境冲突。在终端运行 `python -m pip install <模块名>` 强制给当前运行的 Python 安装。
  2. **代码带框/报错**：AI 生成的代码常带有中文全角逗号或不可见字符，VS Code 会高亮提示。复制后务必检查并替换为英文半角符号。
  3. **Vivado 仿真乱码**：Verilog 的 `$display` 尽量不要输出中文，防止 UTF-8 与系统 GBK 编码冲突。
  4. **Vivado 仿真死循环**：若修改了代码加了 `$finish` 仍不停，且提示 `Invalid Top Module`，说明新代码编译失败，Vivado 加载了旧快照。使用 `Flow -> Reset Behavioral Simulation` 清理缓存后重跑。

### 14. I2C 寄存器回读的正确姿势（repeated start）

- **适用场景**：需要通过 I2C 读回 Codec 寄存器值做验证（如确认某个位真的写进去了）。
- **使用方法 / 踩坑清单**：
  1. **`os.write` + `os.read` 分两次调用不可靠**：Linux 的 i2c-dev 在两次调用之间会发 STOP，很多 Codec 会因此复位内部地址指针，读回的是**写入缓冲区里的内容**（看起来"读到了"，其实是回声）。
  2. **必须用 `I2C_RDWR` 组合消息**：一次 ioctl 内完成"写寄存器地址 + repeated start + 读数据"，才是真正的寄存器读。
  3. **因此"读回等于预期"不能作为写成功的证据**：尤其 PLL 锁定这类判断，回读只是配置镜像。要证明硬件真的生效，必须看**行为**（数据是否在变、时钟是否存在、耳机是否有声）。
- **失效条件**：若 Codec 支持寄存器读且驱动已正确实现 repeated start，则回读可信；但对"状态类"字段（锁定、就绪）仍建议用行为验证。

### 15. I2S 数据线方向核对（sdata_o / sdata_i 绝不能对调）

- **适用场景**：FPGA 作为 I2S Master 与 Codec（ADAU1761 等）对接，通路能"跑通"但采回的数据是错位/无意义的，或示波器上看到数据在动、解码却失败。
- **使用方法 / 踩坑清单**：
  1. **以"方向"而不是"引脚号"命名**：约束里必须写清 `sdata_o`（FPGA→Codec）和 `sdata_i`（Codec→FPGA）。只写 `sdata_0/sdata_in_0` 这类中性名字，一旦有人在 XDC 里调换顺序，RTL 与约束会同时"自洽"，问题极难发现。
  2. **PYNQ-Z2 + ADAU1761 的正确引脚**：`MCLK=U5`、`BCLK=R18`、`LRCLK=T17`、`sdata_o(FPGA→Codec)=G18`、`sdata_i(Codec→FPGA)=F17`。其它引脚（如 `sdata` 与 `sdata_in` 互换）会在电平上"看起来正常"，但数据是错位的。
  3. **优先级高于猜"主从模式"**：Codec 被配成 master 时通常表现为时钟完全缺失、数据全 0；而**数据线接反**表现为时钟正常、累加器有小幅但不随频率变化的读数——两者症状不同，先分辨再动手。
- **失效条件**：若 Codec 是被配置为 Master（由 Codec 提供 BCLK/LRCLK），则 FPGA 侧接收应改为跨时钟域处理，本条的"方向核对"思路仍然成立，但引脚表需按实际板卡重查。

### 16. MCLK / CONVSR / LRCLK 三者必须自洽

- **适用场景**：低频段测量正常、高频段重复性骤降或幅值异常，怀疑数字音频链路有问题。
- **使用方法 / 踩坑清单**：
  1. **先算一遍三个数**：`fs = MCLK / CONVSR`。ADAU1761 的 `CONVERTER0(0x4017)` 中 `CONVSR[2:0]`：`0 → 1024×fs`、`6 → 512×fs`。
  2. **fs 与 LRCLK 必须相等**：LRCLK 是 I2S 帧率（本项目 100 MHz / 2048 = 48.828 kHz）。若 `MCLK/CONVSR ≠ fs_LRCLK`，Codec 内部会按错误的采样率转换，表现为**高频混叠**——低频看着正常、高频重复性极差（实测 cv 可达 64%）。
  3. **反例与修正**：MCLK = 10 MHz + CONVSR = 1024 → fs = 9.77 kHz（差 5 倍）；改为 MCLK = 25 MHz（100 MHz/4）+ CONVSR = 512 → fs = 48.828 kHz，与 LRCLK 严格相等。
  4. **验证方法**：对同一频点重复测 8~10 次算 cv。混叠区的 cv 会明显高于低频段；修好之后各频点 cv 应落在同一量级。
- **失效条件**：若 MCLK 由外部晶振提供而非 FPGA 分频，则"MCLK 可调"这条不成立，只能通过改 CONVSR 或改 LRCLK 分频来对齐。

### 17. 音频通路的"分层验证法"

- **适用场景**：一条"数字 → 模拟 → 数字"的闭环通路出问题，现象混杂（没声音、数据不动、曲线乱跳），不宜直接猜某个模块坏了。
- **使用方法 / 踩坑清单**：
  1. **第一层：数据在不在动**——绕开校准与显示层，直接读最底层的累加器（48 位 `acc_I/acc_Q`）。若读数几乎不变、且幅值贴着满量程的直流量（如恒为 0xFFFF/0x0000），说明**根本没有信号进来**，后面所有"相位/幅值分析"都无意义。
  2. **第二层：时钟对不对**——核算 `MCLK / CONVSR / LRCLK`（见技能十九），并确认 MCLK 引脚上确有波形。
  3. **第三层：模拟开关开没开**——HP 静音位、混音器开关、电源管理（见技能十八）。
  4. **第四层：引脚映射对不对**——数据线方向、I2C 引脚（见技能十六、技能十三）。
  5. **最快的分层工具：直接放一个单音**。绕开扫频状态机，直接写 DDS 频率寄存器输出 1 kHz 到耳机——能听到就证明"数字→模拟"整条链路是通的，问题一定在"模拟→数字"或测量逻辑侧。
- **失效条件**：若通路中不含模拟环节（纯数字环回），则第三层不适用，直接从第一、二、四层排查。

### 18. 双后端验收脚本 (acceptance.py) 的逻辑分离

- **适用场景**：同一个软件接口既要支持官方底层库（如 PYNQ AudioLoopback），又要支持自研 FPGA 链路时。
- **设计规范**：
  1. **字典字段差异**：音频 FFT 库返回的是 `amplitude_code` / `spectral_peak`，而自研链路返回的是 `acc_i` / `acc_q`。两者的字典 Key 完全不同。
  2. **判定分离**：在 `checks` 字典中，应该用 `if backend == "rtl": checks.update(...)` 来区分判定标准。
  3. **RTL 的验收底线**：自研 RTL 链路不依赖频谱峰值，只需满足：`done == True`、`abs(acc_i)+abs(acc_q) > 阈值`（防止纯底噪）、`clock_hz == 预期` 即可 `passed: true`。
- **失效条件**：强制用 FFT 的指标去校验时域 IQ 的输出，会导致“硬件明明通了，软件却判失败”的乌龙。

### 19. Matplotlib 混淆矩阵中文变方块修复

- **适用场景**：在 Linux (PYNQ) 或无中文字体配置的环境中，使用 `matplotlib` 生成带中文标签的混淆矩阵图。
- **使用方法 / 踩坑清单**：
  1. **字体配置**：必须在导入 `matplotlib.pyplot` 之前，强制指定后备字体并修复负号显示。
  2. **配置代码**：`plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "Noto Sans CJK SC", "SimHei", "DejaVu Sans"]` 并且 `plt.rcParams["axes.unicode_minus"] = False`。
- **失效条件**：若运行环境（如极简 Linux）完全没有安装上述任何一种中文字体，图片依然会显示方块。


## 二、RTL / FPGA 设计
