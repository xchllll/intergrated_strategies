`timescale 1ns / 1ps

module i2s_tx (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [15:0] audio_data_L,
    input  wire [15:0] audio_data_R,
    output reg         bclk,
    output reg         lrclk,
    output reg         sdata
);

    reg [4:0] clk_div;
    reg [4:0] bit_index;
    reg       bclk_fall_d;
    reg [15:0] audio_latched;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            clk_div     <= 5'd0;
            bit_index   <= 5'd0;
            bclk_fall_d <= 1'b0;
            audio_latched <= 16'd0;
            bclk        <= 1'b0;
            lrclk       <= 1'b1;
            sdata       <= 1'b0;
        end else begin
            // bclk 下降沿后延迟一个系统时钟，更新 sdata。
            bclk_fall_d <= (clk_div == 5'd31);

            // 32 个 clk 构成一个 bclk 周期：
            // 低电平 16 个 clk，高电平 16 个 clk。
            if (clk_div == 5'd15)
                bclk <= 1'b1;
            else if (clk_div == 5'd31)
                bclk <= 1'b0;

            if (bclk_fall_d) begin
                if (bit_index == 5'd31) begin
                    // 完成一个声道，切换 lrclk。
                    bit_index <= 5'd0;
                    lrclk     <= ~lrclk;
                    // 新声道开始前锁存一个完整样本，发送期间保持不动。
                    audio_latched <= lrclk ? audio_data_R : audio_data_L;
                    sdata     <= 1'b0;
                end else begin
                    bit_index <= bit_index + 5'd1;

                    // bit_index = 0 时输出强制延迟位 0。
                    // bit_index = 1~16 时依次输出 bit15~bit0。
                    // bit_index = 17~31 时输出补零。
                    case (bit_index + 5'd1)
                        5'd1:  sdata <= audio_latched[15];
                        5'd2:  sdata <= audio_latched[14];
                        5'd3:  sdata <= audio_latched[13];
                        5'd4:  sdata <= audio_latched[12];
                        5'd5:  sdata <= audio_latched[11];
                        5'd6:  sdata <= audio_latched[10];
                        5'd7:  sdata <= audio_latched[9];
                        5'd8:  sdata <= audio_latched[8];
                        5'd9:  sdata <= audio_latched[7];
                        5'd10: sdata <= audio_latched[6];
                        5'd11: sdata <= audio_latched[5];
                        5'd12: sdata <= audio_latched[4];
                        5'd13: sdata <= audio_latched[3];
                        5'd14: sdata <= audio_latched[2];
                        5'd15: sdata <= audio_latched[1];
                        5'd16: sdata <= audio_latched[0];
                        default: sdata <= 1'b0;
                    endcase
                end
            end

            if (clk_div == 5'd31)
                clk_div <= 5'd0;
            else
                clk_div <= clk_div + 5'd1;
        end
    end

endmodule
