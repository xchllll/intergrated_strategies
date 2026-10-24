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
    wire signed [15:0] wire_dds_sine;    
    wire signed [15:0] wire_dds_cosine;
    wire signed [47:0] acc_I_full;
    wire signed [47:0] acc_Q_full;  
    wire signed [15:0] rx_left_data;
    wire signed [15:0] rx_right_data;
    wire               rx_data_valid;

    

    dds u_dds (
        .clk(clk), .rst_n(sys_rst_n), .en(1'b1), // 用内部复位
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
            .left_data(rx_left_data), .right_data(rx_right_data), .data_valid(rx_data_valid)
        );
        
    demod u_demod (
        .clk(clk), .rst_n(sys_rst_n), .en(wire_sweep_en), .clear(wire_sweep_clear), 
        .adc_in(rx_left_data), // ★ 改：用真实的左声道数据输入！而不是自环了！
        .ref_cos(wire_dds_cosine), .ref_sin(-wire_dds_sine), 
        .acc_I(acc_I_full), .acc_Q(acc_Q_full)  
    );

    reg [31:0] timer;
    reg [1:0]  state;
    localparam IDLE = 2'd0;
    localparam MEASURE = 2'd1;
    localparam DONE = 2'd2;

    assign out_acc_I_low  = acc_I_full[31:0];
    assign out_acc_I_high = acc_I_full[47:32];
    assign out_acc_Q_low  = acc_Q_full[31:0];
    assign out_acc_Q_high = acc_Q_full[47:32];

    assign out_done = (state == DONE) ? 1'b1 : 1'b0;
    assign wire_sweep_clear = (state == IDLE) ? 1'b1 : 1'b0;
    assign wire_sweep_en = (state == MEASURE) ? 1'b1 : 1'b0;

    always @(posedge clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
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
                    if (timer >= ps_window_max) begin
                        state <= DONE;
                    end else begin
                        timer <= timer + 1;
                    end
                end
                DONE: begin
                    if (!ps_start) begin 
                        state <= IDLE;
                    end
                end
            endcase
        end
    end

    // ★ 新增：简单分频器产生约 12.5MHz 的 MCLK
    reg [2:0] clk_div = 3'd0;  // 加上 = 3'd0
    always @(posedge clk) begin
        clk_div <= clk_div + 1;
    end
    assign au_mclk = clk_div[2]; // 100MHz / 8 = 12.5MHz

endmodule