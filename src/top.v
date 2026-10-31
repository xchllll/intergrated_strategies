module top (
    input  wire        clk,           
    // 删掉 input rst_n，不再依赖外部引脚
    
    input  wire [31:0] ps_phase_inc,  
    input  wire        ps_start,      
    input  wire [31:0] ps_window_max, 
    
    output wire [31:0] out_acc_I_low, 
    output wire [15:0] out_acc_I_high,
    output wire [31:0] out_acc_Q_low, 
    output wire [15:0] out_acc_Q_high,
    output wire        out_done,      
    
    output wire        i2s_bclk,
    output wire        i2s_lrclk,
    output wire        i2s_sdata,
    input  wire        i2s_sdata_in,
    output wire        au_mclk
);

    // 内部复位生成器 (上电后约 2.5us 拉高，启动系统)
    reg [7:0] rst_cnt = 0;
    wire sys_rst_n = (rst_cnt == 8'hFF) ? 1'b1 : 1'b0;
    always @(posedge clk) begin
        if (rst_cnt != 8'hFF) rst_cnt <= rst_cnt + 1;
    end

    wire        wire_sweep_clear; 
    wire        wire_sweep_en;    
    wire        dds_rst_n;          // DDS 相位基准复位（每次测量开始前归零）
    wire signed [15:0] wire_dds_sine;    
    wire signed [15:0] wire_dds_cosine;
    wire signed [47:0] acc_I_full;
    wire signed [47:0] acc_Q_full;  
    wire signed [15:0] rx_left_data;
    wire signed [15:0] rx_right_data;
    wire signed [15:0] rx_mix_data;      // L+R（饱和），夹具接 Tip 或 Ring 都能测
    wire               rx_data_valid;

    // ------------------------------------------------------------------
    // 结果锁存器（屏蔽软件时序竞争，例如 start 被误拉低导致状态机退回
    // IDLE、demod 的 clear 生效而把 acc 清掉）。
    // 一旦一次测量结束，就把 demod 的最终累加值复制到这里；此后无论
    // demod 的 acc 怎么变，对外输出的都是这份稳定快照。
    // ------------------------------------------------------------------
    reg [47:0] latched_acc_I;
    reg [47:0] latched_acc_Q;
    reg        acc_latched;   // 本次测量结果已锁存（防止重复锁存 / DONE 期间被覆盖）

    

    dds u_dds (
        .clk(clk), .rst_n(dds_rst_n), .en(1'b1), // 用测量窗口复位，保证相位从 0 开始
        .phase_inc(ps_phase_inc), 
        .sine(wire_dds_sine), .cosine(wire_dds_cosine)   
    );

    i2s_tx u_i2s_tx (
        .clk(clk), .rst_n(sys_rst_n), // 用内部复位
        .audio_data_L(wire_dds_sine), .audio_data_R(wire_dds_sine), 
        .bclk(i2s_bclk), .lrclk(i2s_lrclk), .sdata(i2s_sdata)
    );

    i2s_rx u_i2s_rx (
            .clk(clk), .rst_n(sys_rst_n), // 用内部复位
            .i2s_bclk(i2s_bclk), .i2s_lrclk(i2s_lrclk), .i2s_sdata_in(i2s_sdata_in),
            .left_data(rx_left_data), .right_data(rx_right_data),
            .mix_data(rx_mix_data), .data_valid(rx_data_valid)
        );
        
    demod u_demod (
        .clk(clk), .rst_n(sys_rst_n), .en(wire_sweep_en), .clear(wire_sweep_clear), 
        .adc_in(rx_mix_data),  // 左右声道相加（饱和）：夹具接 Tip 或 Ring 都能测
        .ref_cos(wire_dds_cosine), .ref_sin(-wire_dds_sine), 
        .acc_I(acc_I_full), .acc_Q(acc_Q_full)  
    );

    reg [31:0] sample_count;
    reg [1:0]  state;
    localparam IDLE = 2'd0;
    localparam MEASURE = 2'd1;
    localparam FLUSH = 2'd2;   // 积分窗满后多停一拍，把最后一个 mix 压进 acc
    localparam DONE = 2'd3;

    // ★ 对外输出改为锁存值：MEASURE / FLUSH 期间输出保持为 0（或上一次的旧值），
    //   不会把半途的累加结果暴露给软件；DONE 之后输出的是整窗积分快照。
    assign out_acc_I_low  = latched_acc_I[31:0];
    assign out_acc_I_high = latched_acc_I[47:32];
    assign out_acc_Q_low  = latched_acc_Q[31:0];
    assign out_acc_Q_high = latched_acc_Q[47:32];

    // 只有结果真正落进锁存器之后才置起 done，避免软件在"状态刚到 DONE、
    // 但锁存还没更新"的那一拍读到旧值/0。
    assign out_done = (state == DONE) && acc_latched;
    assign wire_sweep_clear = (state == IDLE) ? 1'b1 : 1'b0;
    // FLUSH 期间必须继续使能 demod 一拍。demod 是「乘积打一拍、累加打一拍」
    // 的流水线：最后一个样本的乘积在窗满那一拍进 mix，要到下一拍才进 acc。
    // 注意这里不能与 rx_data_valid 相与——valid 每 2048 个时钟才高一个周期，
    // FLUSH 那一拍 valid 已经是低电平，与上就永远 flush 不进去。
    assign wire_sweep_en = ((state == MEASURE) && rx_data_valid) || (state == FLUSH);

    // ★ 关键修改：DDS 在每个测量窗口开始前都被复位到 0 相位。
    //   IDLE 期间 dds_rst_n 一直为低，DDS 的 phase_acc 和 sine/cosine 保持 0；
    //   状态机进入 MEASURE 后释放复位，DDS 从 phase_acc = 0 开始累加，
    //   因此每个频点的相位基准都是绝对固定的，不再受上电/启动时刻影响。
    //   同时该复位与 demod 的 clear（IDLE 有效）保持同一时间基准。
    assign dds_rst_n = sys_rst_n && (state != IDLE);

    always @(posedge clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            state <= IDLE;
            sample_count <= 0;
        end else begin
            case (state)
                IDLE: begin
                    if (ps_start) begin
                        state <= MEASURE;
                        sample_count <= 0;
                    end
                end
                MEASURE: begin
                    if (rx_data_valid) begin
                        if (sample_count + 1'b1 >= ps_window_max) begin
                            // 积分窗已满：最后一个样本的乘积刚刚进入 demod.mix，
                            // 先去 FLUSH 停一拍把它加进 acc，再进 DONE
                            state <= FLUSH;
                        end else begin
                            sample_count <= sample_count + 1'b1;
                        end
                    end
                end
                FLUSH: begin
                    // 固定停留 1 个时钟。这一拍 wire_sweep_en 仍为高，
                    // demod 执行 acc <= acc + mix，补上最后一个样本。
                    state <= DONE;
                end
                DONE: begin
                    if (!ps_start) begin 
                        state <= IDLE;
                    end
                end
            endcase
        end
    end

    // ------------------------------------------------------------------
    // 结果锁存
    // ------------------------------------------------------------------
    // 触发点说明：
    //   积分窗满的那一拍（state == MEASURE && rx_data_valid &&
    //   sample_count + 1 >= ps_window_max）只是「最后一个样本的乘积进入
    //   demod.mix」；demod 是流水线结构，这一项要到下一拍才累加进 acc。
    //   因此状态机先进入 FLUSH 停留一拍（该拍 en 仍为高，acc 补上最后一项），
    //   再进入 DONE。本模块在进入 DONE 的第一拍把 acc 搬到锁存器，此时 acc
    //   已经包含完整的 N 个样本。DONE 期间 en=0、clear=0，acc 保持不变。
    //
    // 清零策略：
    //   在 IDLE 且 ps_start 有效（即即将启动新一次测量）时清零。这样既满足
    //   "新测量开始前清零"，又能让"结果在 DONE 之后一直保留到下一次启动"，
    //   即使软件把 start 误拉低、状态机退回 IDLE，锁存值也不会被抹掉。
    //   若确实希望"只要处于 IDLE 就清零"，把下面 if (ps_start) 改成 if (1'b1)
    //   即可（但那样会重新引入 start 抖动清掉有效结果的隐患）。
    always @(posedge clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            latched_acc_I <= 48'd0;
            latched_acc_Q <= 48'd0;
            acc_latched   <= 1'b0;
        end else if (state == IDLE) begin
            if (ps_start) begin
                latched_acc_I <= 48'd0;
                latched_acc_Q <= 48'd0;
                acc_latched   <= 1'b0;
            end
        end else if ((state == DONE) && !acc_latched) begin
            latched_acc_I <= acc_I_full;
            latched_acc_Q <= acc_Q_full;
            acc_latched   <= 1'b1;
        end
    end

    // ADU1761 PLL is configured for a 10 MHz MCLK reference.
    // FCLK_CLK0 = 100 MHz, so a 10-cycle pattern (5 high / 5 low)
    // produces 10 MHz MCLK while the I2S divider keeps ~48.8 kSa/s.
    reg [3:0] mclk_div = 4'd0;
    always @(posedge clk) begin
        if (!sys_rst_n)
            mclk_div <= 4'd0;
        else if (mclk_div == 4'd3)
            mclk_div <= 4'd0;
        else
            mclk_div <= mclk_div + 1'b1;
    end
    assign au_mclk = (mclk_div < 4'd2);

endmodule
