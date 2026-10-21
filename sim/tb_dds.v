`timescale 1ns/1ps

module tb_dds;
    reg clk = 0;
    reg rst_n = 0;
    reg en = 0;
    reg [31:0] phase_inc = 32'd42950; 
    wire signed [15:0] sine;
    wire signed [15:0] cosine;

    dds dut (
        .clk(clk), 
        .rst_n(rst_n), 
        .en(en), 
        .phase_inc(phase_inc), 
        .sine(sine),
        .cosine(cosine)
    );

    always #5 clk = ~clk;

    integer i;
    initial begin
        $dumpfile("tb_dds.vcd");
        $dumpvars(0, tb_dds);
        
        rst_n = 0;
        #100; 
        rst_n = 1;
        en = 1;
        
        for (i = 0; i < 200000; i = i + 1) begin
            @(posedge clk); 
        end
        $finish;
    end
endmodule