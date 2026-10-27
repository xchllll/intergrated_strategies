# 大模型协作记录 (Large Model Collaboration Log)

本记录截取了团队在初期环境搭建与核心模块（DDS）验证阶段，与 AI 助手进行工程协作的真实轨迹。记录重点展示了在遇到真实物理工具链报错时，团队的排错思路与自我纠错过程。

| 轮次 | 用户提示词 (Prompt) | 模型回答摘要 | 自我纠错 / 验证过程 |
| :--- | :--- | :--- | :--- |
| 1 | 仓库建好了，但 git clone 报错 `fatal: repository not found`，浏览器能打开，代理也开了。 | AI 分析了可能是仓库为 Private（私有）且终端未登录，或者是 Windows 凭据管理器中存在旧账号冲突。建议清理凭据或改公开。 | **验证**：我去检查发现确实是权限问题，并发现电脑里存在旧的 GitHub 凭据。通过清除系统凭据并重新授权，成功拉取了代码。 |
| 2 | `dds.v` 代码在 Vivado 里跑仿真，为什么 `sine` 波形上面有断崖，变成了一个个“驼峰”？ | AI 指出因为正弦波是 16位补码（有符号数），Vivado 默认按无符号（Unsigned）显示，导致负数被当成了极大的正数翻转到了上面。 | **验证**：按 AI 提示，右键 `sine` 信号，将 Radix 改为 `Signed Decimal`（有符号十进制），再将波形样式改为 `Analog`（模拟）。重新运行后，成功看到了完美平滑的正弦波。 |
| 3 | 解调模块能不能直接在 Verilog 里写浮点乘法？ | AI 明确拒绝，指出 FPGA 综合工具无法高效生成浮点乘法器，且资源消耗巨大。要求先用 Python 做定点仿真，确定位宽和截断误差。 | **自我纠错**：我意识到直接写 RTL 容易因为位宽溢出导致数据错乱。采纳了“先 Python 建模，后 RTL 实现”的标准工程流程。 |
| 4 | 复制了 AI 给的 Python 代码，VS Code 报错并且标点符号带框，为什么？ | AI 承认生成代码时混入了全角（中文）逗号，导致 Python 解释器无法识别。给出了修正后的纯净半角代码。 | 自我纠错/验证：我立刻检查输入法状态，并提醒团队：在复制 AI 代码后，必须人工二次审查标点符号。后续代码严格限定使用半角英文标点。 |
| 6 | 运行 Python 代码报错 `ModuleNotFoundError: No module named 'numpy'`。 | 提示代码依赖第三方数学库，当前环境未安装。建议执行 `pip install numpy`。 | **自我纠错/验证**：发现电脑有多个 Python 环境，pip 把库装到了 D 盘的虚拟环境，而 VS Code 运行的是 C 盘 Python。改用 `python -m pip install numpy` 成功安装依赖并运行。 |
| 7 | 复制 AI 给的 Python 代码，VS Code 报错且标点带框，无法运行。 | AI 承认生成代码时混入了全角（中文）逗号，导致语法错误。提供修正后的英文半角代码。 | **验证**：重新复制纯净代码运行成功。团队确立原则：复制 AI 代码后，必须人工复查标点符号，严禁中文标点。 |
| 8 | Verilog 测试台代码行首出现红色方块警告。 | 分析为复制代码时混入了不可见的全角空格或控制字符。 | **验证**：删除整行重新手动输入半角空格解决。 |
| 9 | 在 Vivado 跑 Verilog 仿真（`tb_demod.v`），报 `Invalid Top Module` 且仿真停不下来。 | 指出因为代码中存在全角字符或语法错误导致编译失败，Vivado 偷偷加载了旧代码快照（没有 `$finish`）导致死循环。 | **验证**：通过 `Flow -> Reset Behavioral Simulation` 清理旧缓存，并排查纯英文代码后，仿真正常停止，验证 `acc_I` 结果为 1.73 亿，与 Python 理论值一致。 |
| 10 | 仿真结束，Tcl Console 打印的中文显示为乱码。 | 解释为 Vivado XSim 与 Windows 控制台的字符编码（UTF-8 与 GBK）冲突。 | **验证**：放弃依赖终端打印，直接查看波形（Waveform）中 `acc_I` 的最终十进制值。确立行业规范：Verilog 打印（`$display`）统一只用纯英文。 |
| 11 | 如何在 Verilog 中实现一个控制扫频的状态机？ | 提供了标准的“三段式状态机（FSM）”代码框架，并解释了组合逻辑（`always @(*)`）和时序逻辑（`always @(posedge clk)`）的分工。 | **自我纠错/验证**：我深刻理解了如果用时序逻辑写状态转移的第二段，会导致每个状态被迫多停留一个时钟周期，从而全盘错乱。通过 Testbench 验证了 `IDLE -> CLEAR -> MEASURE -> DONE` 的正确跳转。 |
| 12 | 仿真跑得太慢怎么办？ | 建议把状态机里的计时器参数 `TIME_MAX` 临时改小（如从 10000 改为 5），仅供仿真快速查看波形。 | **验证**：修改参数后，仿真瞬间跑完，在波形上成功捕捉到了 `clear` 的单周期脉冲和 `freq_idx` 的计数。 |
| 13 | 扫频状态机里的 `freq_idx` 如果加到 240 以上会爆表吗？需要加防护吗？ | AI 算了位宽账：8位无符号数最大 255，240 装得下。但强烈建议加上 `if (freq_idx < 8'd239)` 的防呆逻辑，防止状态机跑飞导致逻辑越界。 | **自我纠错/验证**：我意识到在硬件设计中，即使当前值没溢出，也需要防范跑飞风险。加上了 `< 239` 的防呆限制，并将此确立为安全设计规范。 |
| 14 | 为什么 `always @(*)` 里的代码是瞬间执行的？如果把第二段状态机写成了带 `posedge clk` 的时序逻辑会怎样？ | AI 解释了组合逻辑（Combinational Logic）的概念。并指出如果状态跳转用时序逻辑，会导致每个状态被迫多停留一个时钟周期，整个状态机的时间轴全盘错乱。 | **验证**：与我的直觉完全吻合。我明白了“第一段管跳转，第二段算次态（组合），第三段管输出”的三段式铁律。 |
| 15 | 跑扫频仿真时，Vivado 报错 `boost::filesystem::remove: 另一个程序正在使用此文件`。 | 提示是上一次的仿真进程（`xsimk.exe`）未彻底关闭，占用了 `simulate.log` 文件。建议在任务管理器中结束进程，或在 Vivado 中执行 `Close Simulation`。 | **自我纠错/验证**：手动执行了 `Flow -> Close Simulation`，释放了文件占用，重新运行成功。 |
| 16 | 仿真跑出来，左侧波形窗口里找不到 `state` 和 `timer` 信号。 | 因为当前在 `tb_sweep` 顶层，内部信号被封装在实例化的 `dut` 模块里。需要双击 Scope 窗口里的 `dut` 才能看到内部寄存器。 | **验证**：点击 `dut` 后，成功找到 `state` 并添加到波形窗口，观察到 `0->1->2->3` 的完美跳转。 |
| 17 | I2S发送模块仿真时，波形数据出现严重错位和重叠。AI（助手A）多次修改代码（移位寄存器法、穷举法），但仍出现"一个bclk周期内数据变两次"的竞争冒险现象。 | 助手A尝试调整数据更新时钟（错开半周期），但仍未彻底解决。建议寻求交叉验证。 | **自我纠错/验证**：我提取了核心需求，将两个文件交由另一个独立AI（助手B）重新生成。助手B使用 `bclk_fall_d` 延迟一拍和 `active_audio` 动态选择的方法，并提供了带 `Self-Checking`（自动断言校验）的Testbench。经Vivado仿真，控制台输出 `PASS`，波形完全符合I2S时序。 |
| 18 | 在更换AI生成的测试台中，是如何自动验证数据正确性的？ | 助手B的Testbench在 `posedge bclk` 时，会根据 `slot_index` 实时计算 `expected_sdata`，一旦 `sdata !== expected_sdata`，立即报错。 | **验证**：我意识到 `Self-Checking Testbench` 比人工观察波形或导出txt文件效率高得多。将此方法收入团队技能包。 |
| 19 | 在 Vivado Block Design 中，如何将 48 位的 `acc_I` 和 `acc_Q` 传给 32 位的 AXI GPIO？ | AI 最初建议在 BD 里拆分位宽或者加 Slice IP，导致连线极其混乱且容易报错。 | **自我纠错/验证**：我意识到在 RTL 层解决更干净。直接在 `top.v` 中把 48 位输出拆分为 `out_acc_I_low[31:0]` 和 `out_acc_I_high[15:0]`，BD 自动刷新后连线极其清晰，彻底避免了位宽不匹配的 DRC 报错。 |
| 20 | 测试代码烧录后，`out_done` 信号始终为 0，读回的数据也全是 0。 | AI 推测可能是状态机逻辑卡死，或者积分窗口没写对。 | **自我纠错/验证**：经检查，是 `top.v` 的 `rst_n` 绑到了外部引脚，但板子上没接实体按键，导致系统一直处于复位状态。将 `top.v` 改为内部计数器自动复位（上电后数 255 个时钟周期自动释放）后，硬件成功启动。 |
| 21 | PYNQ 运行 Python 报错：`AxiGPIO.Channel.write() missing 1 required positional argument: 'mask'` | AI 提示这是 PYNQ 新版 API 变动，写寄存器需要指定位掩码（Mask）。 | **验证**：在写入函数中加入掩码参数：`gpio.channel1.write(value, 0xFFFFFFFF)` 后成功写入硬件。 |
| 22 | Python 拼接 48 位数据后，算出来的 I/Q 值是几百万亿的超级正数，不符合理论负数结果。 | AI 指出 Python 默认按无符号整数处理，高 16 位如果是 1，需要手动处理 48 位有符号补码。 | **验证**：加入判断 `if acc_I >= (1 << 47): acc_I -= (1 << 48)`，成功还原了正确的负数值，与理论推导完美吻合。 |
| 23 | 在 block design 中，i2s_sdata_in 等引脚总是提示 tied-off to all 0's。 | AI 建议在 BD 中手动 Make External 并绑定引脚。 | **验证**：刷新模块后，将新增端口 Make External，并在 `top.xdc` 中补充了对应的真实管脚约束（如 G18、U5），彻底消除了悬空警告。 |
# 大模型协作记录 (Large Model Collaboration Log) - P0 阶段

| 轮次 | 用户提示词 (Prompt) | 模型回答摘要 | 自我纠错 / 验证过程 |
| :--- | :--- | :--- | :--- |
| 24 | 驱动写完后发现 `read_IQ()` 读不到 `out_acc_I/Q`，是代码问题还是硬件问题？ | AI 复查 hwh 端口连接表，发现 `top.v` 的 `out_acc_I[47:0]`、`out_acc_Q[47:0]`、`out_done` 在 Block Design 中**未接入任何 AXI GPIO（悬空）**，属硬件接线缺口而非代码错误；同时指出 `ps_start` 拉低后状态机回 IDLE 会把累加器清零，取数必须在 start 仍为高时进行。 | **验证**：确认根因后给出方案——在 Block Design 补接读取 GPIO，并新增 `measure_once()` 便捷接口规避清零时序，脚本保留可配置常量待接入后填写。 |
| 25 | 写 `calibration.py` 把 48 位 `acc_I/Q` 还原成 V 和度时，定点截断系数按哪个口径？ | AI 以 `python_model/demod_sim.py` 为唯一口径：`norm_factor = N × 10000 × 32767`；注意到该文件积分点数 N 已从 1000 改为 100，归一化因子随之变化，故把 N 等系数集中定义为常量并标注“与模型单点同步”。 | **验证**：先在本地用 demod_sim.py 的验收数据反推定点累加值，重算 `I_float/Q_float` 与幅值相位公式确认一致；校准表留空，由 `four_state_calibrate()` 待上板后用 4 个已知参考态自动填充。 |
| 26 | 加载自定义 XSA 后，`i2c-1` 扫描 0x3B 报 `Errno 6 (No such device or address)`。之前用官方 base.bit 是能扫描到的。 | AI 对比了原理图与交接文档，指出文档中写的 `SCL=P16, SDA=P15` 是 Arduino 扩展引脚的 I2C，而非板载 ADAU1761 的 I2C。ADAU1761 的实际引脚为 `SCL=U9, SDA=T9`。 | **验证**：修改 XDC 重新生成 Bitstream，加载后 `os.write` 成功不再报 Errno 6，成功通过 I2C 配置了 codec 寄存器。 |
| 27 | 硬件模块配置完成拉高 start 后，调用 `ps_ctrl.measure_once()` 始终报 `out_done` 超时。 | AI 计算了积分时间（N=488约10ms），指出 `measure_once` 默认的 `margin_s=1e-3`（1ms）太短，会被 Python 的 `time.sleep` 和 MMIO 读取开销吃掉，建议将 margin 改为 0.05 (50ms)。 | **验证**：T1 修改后单元测试通过。但在板子上因旧版 `acceptance.py` 会加载 base.bit 导致硬件被覆盖，仍报超时。后通过手搓 MMIO 寄存器测出了真实的 `acc_I=-69786, acc_Q=22854`，证实硬件逻辑无误。 |
| 28 | 跑 `acceptance.py --backend rtl`，硬件已读到非零 `acc_I` 和 `acc_Q`，但判定 `passed: false`，报 `physical_signal` 和 `frequency_locked` 失败。 | AI 审查了 checks 逻辑，指出 `physical_signal` 是依赖 `amplitude_code`（FFT频谱分析）的字段，在 RTL 后端的字典里根本不存在。建议分离 RTL 和 audio_loopback 的判定逻辑。 | **验证**：确认为测试脚本逻辑 Bug。交由 T1 修改 `acceptance.py`，区分双后端的判定标准。 |
## 阶段总结
在初期协作中，大模型在代码语法（如 DDS 的查表与相位累加器结构）和工具链排错（Vivado 波形显示机制、Git 凭据配置）上提供了有效辅助。但团队成员对工程边界（如 FPAG 不能写浮点）和真实环境（Windows 凭据冲突）的把关，保证了项目的正确推进。