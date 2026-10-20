`timescale 1ns / 1ps

module tb_demod;
    reg clk;
    reg rst_n;
    reg en;
    reg clear;
    reg signed [15:0] adc_in;
    reg signed [15:0] ref_cos;
    reg signed [15:0] ref_sin;
    
    wire signed [47:0] acc_I;
    wire signed [47:0] acc_Q;

    // 实例化解调模块 (DUT)
    demod dut (
        .clk(clk), 
        .rst_n(rst_n), 
        .en(en), 
        .clear(clear),
        .adc_in(adc_in), 
        .ref_cos(ref_cos), 
        .ref_sin(ref_sin),
        .acc_I(acc_I), 
        .acc_Q(acc_Q)
    );

    // 100MHz 时钟生成
    initial clk = 0;
    always #5 clk = ~clk;

    // 数学常量定义
    real pi = 3.1415926535;
    real t;
    integer i;

    initial begin
        $dumpfile("tb_demod.vcd");
        $dumpvars(0, tb_demod);

        // 初始化
        rst_n = 0; en = 0; clear = 0;
        adc_in = 0; ref_cos = 0; ref_sin = 0;
        #100;
        rst_n = 1;
        #20; 

        // 开始积分 (模拟 1000 个点)
        en = 1; 
        for (i = 0; i < 1000; i = i + 1) begin
            t = i * 10.0e-6; // 100kHz 采样率 (周期 10us)
            // 用 $cos 和 $sin 生成与 Python 完全相同的定点数
            adc_in = $rtoi(1.5 * $cos(2.0 * pi * 1000.0 * t + pi/4.0) * 10000.0);
            ref_cos = $rtoi($cos(2.0 * pi * 1000.0 * t) * 32767.0);
            ref_sin = $rtoi(-$sin(2.0 * pi * 1000.0 * t) * 32767.0);
            
            @(posedge clk); // 等待下一个时钟沿
        end
        
        en = 0;
        #100;
        
        $display("================ Verify Result ================");
        $display("acc_I = %d", acc_I);
        $display("acc_Q = %d", acc_Q);
        $display("================================================");
        
        $finish; // 结束仿真
    end

endmodule