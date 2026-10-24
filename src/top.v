module top (
    input  wire        clk,           
    input  wire        rst_n,         
    
    input  wire [31:0] ps_phase_inc,  
    input  wire        ps_start,      
    input  wire [31:0] ps_window_max, 
    
    // ★ 改：把 48 位拆成高低位输出
    output wire [31:0] out_acc_I_low,  // I 路低 32 位
    output wire [15:0] out_acc_I_high, // I 路高 16 位
    output wire [31:0] out_acc_Q_low,  // Q 路低 32 位
    output wire [15:0] out_acc_Q_high, // Q 路高 16 位
    output wire        out_done,      
    
    output wire        i2s_bclk,
    output wire        i2s_lrclk,
    output wire        i2s_sdata
);

    wire        wire_sweep_clear; 
    wire        wire_sweep_en;    
    wire signed [15:0] wire_dds_sine;    
    wire signed [15:0] wire_dds_cosine;
    wire signed [47:0] acc_I_full;
    wire signed [47:0] acc_Q_full;  

    dds u_dds (
        .clk(clk), .rst_n(rst_n), .en(1'b1), 
        .phase_inc(ps_phase_inc), 
        .sine(wire_dds_sine), .cosine(wire_dds_cosine)   
    );

    i2s_tx u_i2s_tx (
        .clk(clk), .rst_n(rst_n), 
        .audio_data_L(wire_dds_sine), .audio_data_R(wire_dds_sine), 
        .bclk(i2s_bclk), .lrclk(i2s_lrclk), .sdata(i2s_sdata)
    );

    demod u_demod (
        .clk(clk), .rst_n(rst_n), .en(wire_sweep_en), .clear(wire_sweep_clear), 
        .adc_in(wire_dds_sine), // 暂时保留自环，等 I2S RX 写好再换
        .ref_cos(wire_dds_cosine), .ref_sin(-wire_dds_sine), 
        .acc_I   (acc_I_full), // 接内部线
        .acc_Q   (acc_Q_full)  // 接内部线
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
                    // 这里的比较对象换成了 PS 传入的 ps_window_max
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
endmodule