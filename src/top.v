module top (
    input  wire        clk,           // 100MHz 系统时钟
    input  wire        rst_n,         // 复位
    
    // --- 给 PS 控制的接口 (接 AXI GPIO 输出) ---
    input  wire [31:0] ps_phase_inc,  // PS 写入的频率控制字
    input  wire        ps_start,      // PS 写入的启动/停止信号
    
    // --- 给 PS 读取的接口 (接 AXI GPIO 输入) ---
    output wire [47:0] out_acc_I,     // 测量结果 I
    output wire [47:0] out_acc_Q,     // 测量结果 Q
    output wire        out_done,      // 测量完成标志
    
    // --- 物理引脚 ---
    output wire        i2s_bclk,
    output wire        i2s_lrclk,
    output wire        i2s_sdata
);

    // 内部连线
    wire        wire_sweep_clear; 
    wire        wire_sweep_en;    
    wire signed [15:0] wire_dds_sine;    
    wire signed [15:0] wire_dds_cosine;  

    // 1. 发波机 (DDS)
    dds u_dds (
        .clk       (clk),
        .rst_n     (rst_n),
        .en        (1'b1),             
        .phase_inc (ps_phase_inc),     // ★ 改：由 PS 直接提供频率
        .sine      (wire_dds_sine),    
        .cosine    (wire_dds_cosine)   
    );

    // 2. 音频发送机 (I2S TX)
    i2s_tx u_i2s_tx (
        .clk          (clk),
        .rst_n        (rst_n),
        .audio_data_L (wire_dds_sine), 
        .audio_data_R (wire_dds_sine), 
        .bclk         (i2s_bclk),
        .lrclk        (i2s_lrclk),
        .sdata        (i2s_sdata)
    );

    // 3. 测量仪 (Demod)
    demod u_demod (
        .clk     (clk),
        .rst_n   (rst_n),
        .en      (wire_sweep_en),      
        .clear   (wire_sweep_clear),   
        .adc_in  (wire_dds_sine),      // 自环测试
        .ref_cos (wire_dds_cosine),    
        .ref_sin (-wire_dds_sine),     
        .acc_I   (out_acc_I),
        .acc_Q   (out_acc_Q)
    );

    // 4. 简化版的“测量控制状态机” (代替原来的 sweep_ctrl)
    // 我们不再自动扫 240 个点，而是通过 PS 的 ps_start 信号来触发一次测量
    reg [31:0] timer;
    reg [1:0]  state;
    localparam IDLE = 2'd0;
    localparam MEASURE = 2'd1;
    localparam DONE = 2'd2;
    
    localparam TIME_MAX = 32'd10000; // 积分窗口长度

    assign out_done = (state == DONE) ? 1'b1 : 1'b0;
    assign wire_sweep_clear = (state == IDLE) ? 1'b1 : 1'b0;
    assign wire_sweep_en = (state == MEASURE) ? 1'b1 : 1'b0;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            timer <= 0;
        end else begin
            case (state)
                IDLE: begin
                    if (ps_start) begin
                        state <= MEASURE;
                        timer <= 0;
                    end
                end
                MEASURE: begin
                    if (timer >= TIME_MAX) begin
                        state <= DONE;
                    end else begin
                        timer <= timer + 1;
                    end
                end
                DONE: begin
                    if (!ps_start) begin // 等 PS 把 ps_start 拉低后，再回到待机
                        state <= IDLE;
                    end
                end
            endcase
        end
    end

endmodule