`timescale 1ns / 1ps

module tb_sweep;
    reg clk;
    reg rst_n;
    reg start;
    
    wire [31:0] phase_inc;
    wire        clear;
    wire        en;
    wire        done;
    wire [7:0]  freq_idx;

    // 实例化被测模块
    sweep_ctrl dut (
        .clk(clk),
        .rst_n(rst_n),
        .start(start),
        .phase_inc(phase_inc),
        .clear(clear),
        .en(en),
        .done(done),
        .freq_idx(freq_idx)
    );

    // 100MHz 时钟
    initial clk = 0;
    always #5 clk = ~clk;

    // 测试激励
    initial begin
        // 记录波形
        $dumpfile("tb_sweep.vcd");
        $dumpvars(0, tb_sweep);

        // 初始化
        rst_n = 0;
        start = 0;
        #100;
        
        // 释放复位
        rst_n = 1;
        #50;
        
        // 拉高 start，启动扫频
        start = 1;
        #20;
        start = 0; // 给一个脉冲后拉低

        // 等待足够长的时间，让它跑完几个完整的频点
        // 因为 TIME_MAX 改成了 5，跑一个点大概需要几十 ns
        #2000; 

        $display("================ TB FINISHED ================");
        $finish;
    end
endmodule