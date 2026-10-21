module top (
    input  wire        clk,           // 100MHz 系统时钟
    input  wire        rst_n,         // 复位
    input  wire        start,         // 启动扫频
    
    // I2S 物理引脚
    output wire        i2s_bclk,
    output wire        i2s_lrclk,
    output wire        i2s_sdata,
    
    // 输出给 PS 侧的数据
    output wire [47:0] out_acc_I,     // I 路解调结果
    output wire [47:0] out_acc_Q,     // Q 路解调结果
    output wire        out_done       // 扫频完成标志
);

    // ==========================================
    // 1. 内部连线声明 (纯导线)
    // ==========================================
    wire [31:0] wire_phase_inc;   
    wire        wire_sweep_clear; 
    wire        wire_sweep_en;    
    
    wire signed [15:0] wire_dds_sine;    
    wire signed [15:0] wire_dds_cosine;  

    // ==========================================
    // 2. 扫频指挥官 (Sweep Controller)
    // ==========================================
    sweep_ctrl u_sweep_ctrl (
        .clk       (clk),
        .rst_n     (rst_n),
        .start     (start),
        .phase_inc (wire_phase_inc),   
        .clear     (wire_sweep_clear), 
        .en        (wire_sweep_en),    
        .done      (out_done),
        .freq_idx  ()                  
    );

    // ==========================================
    // 3. 发波机 (DDS) - 输出正弦和余弦
    // ==========================================
    dds u_dds (
        .clk       (clk),
        .rst_n     (rst_n),
        .en        (1'b1),             // DDS 常开
        .phase_inc (wire_phase_inc),   
        .sine      (wire_dds_sine),    
        .cosine    (wire_dds_cosine)   
    );

    // ==========================================
    // 4. 音频发送机 (I2S TX) - 把发出的正弦波变成 I2S 信号
    // ==========================================
    i2s_tx u_i2s_tx (
        .clk          (clk),
        .rst_n        (rst_n),
        .audio_data_L (wire_dds_sine), 
        .audio_data_R (wire_dds_sine), 
        .bclk         (i2s_bclk),
        .lrclk        (i2s_lrclk),
        .sdata        (i2s_sdata)
    );

    // ==========================================
    // 5. 测量仪 (Demod) - 接入正交信号进行解调
    // ==========================================
    demod u_demod (
        .clk     (clk),
        .rst_n   (rst_n),
        .en      (wire_sweep_en),      
        .clear   (wire_sweep_clear),   
        
        // 【自环测试】假将被测信号设为发出的正弦波
        .adc_in  (wire_dds_sine),      
        
        // 【正交本振】
        .ref_cos (wire_dds_cosine),    // I路：接余弦
        .ref_sin (-wire_dds_sine),     // Q路：接负正弦 (加负号取反)
        
        .acc_I   (out_acc_I),
        .acc_Q   (out_acc_Q)
    );

endmodule