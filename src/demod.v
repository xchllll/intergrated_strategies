module demod (
    input  wire        clk,        // Clock (系统时钟)
    input  wire        rst_n,      // Reset Negative (低电平复位)
    input  wire        en,         // Enable (采样使能，比如 48kHz)
    input  wire        clear,      // Clear (清零信号，积分完一次后清零，准备下一次)
    
    input  wire signed [15:0] adc_in,   // ADC 输入的信号 (16位有符号)
    input  wire signed [15:0] ref_cos,  // DDS 产生的 I 路本振 (16位有符号)
    input  wire signed [15:0] ref_sin,  // DDS 产生的 Q 路本振 (16位有符号)
    
    output reg signed [47:0] acc_I,     // I 路累加器输出 (48位有符号)
    output reg signed [47:0] acc_Q      // Q 路累加器输出 (48位有符号)
);

    // 内部寄存器定义 (用来存混频乘法器的结果)
    reg signed [31:0] mix_I;
    reg signed [31:0] mix_Q;

    // 核心逻辑：混频与累加
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            // 复位清零
            mix_I <= 32'd0;
            mix_Q <= 32'd0;
            acc_I <= 48'd0;
            acc_Q <= 48'd0;
        end else if (clear) begin
            // 收到 clear 信号时，累加器清零 (为下一个频点做准备)
            acc_I <= 48'd0;
            acc_Q <= 48'd0;
        end else if (en) begin
            // 1. 混频 (16位 * 16位 = 32位)
            mix_I <= adc_in * ref_cos;
            mix_Q <= adc_in * ref_sin;
            
            // 2. 积分累加 (48位)
            acc_I <= acc_I + mix_I;
            acc_Q <= acc_Q + mix_Q;
        end
    end

endmodule