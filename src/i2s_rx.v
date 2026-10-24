module i2s_rx (
    input  wire        clk,           // 100MHz 系统时钟
    input  wire        rst_n,         // 低电平复位
    input  wire        i2s_bclk,      // 位时钟
    input  wire        i2s_lrclk,     // 左右声道时钟
    input  wire        i2s_sdata_in,  // 从音频芯片接回来的串行数据
    
    output reg signed [15:0] left_data,   // 解析出的左声道数据
    output reg signed [15:0] right_data,  // 解析出的右声道数据
    output reg               data_valid   // 数据更新有效脉冲 (每次采完一个声道给一个脉冲)
);

    // 1. 时钟边沿检测：我们需要抓取 i2s_bclk 的上升沿来采样数据
    reg bclk_d1, bclk_d2;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bclk_d1 <= 1'b0;
            bclk_d2 <= 1'b0;
        end else begin
            bclk_d1 <= i2s_bclk;
            bclk_d2 <= bclk_d1;
        end
    end
    wire bclk_rising = (bclk_d1 && !bclk_d2); // 检测上升沿

    // 2. 声道计数器 (记录当前是第几个 bit，0~31)
    reg [4:0] bit_cnt;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bit_cnt <= 5'd0;
        end else if (bclk_rising) begin
            if (bit_cnt == 5'd31)
                bit_cnt <= 5'd0;
            else
                bit_cnt <= bit_cnt + 1'b1;
        end
    end

    // 3. 移位寄存器 (存放正在接收的 16 位数据)
    reg [15:0] shift_reg;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            shift_reg <= 16'd0;
        end else if (bclk_rising) begin
            // 把新来的 bit 放在最低位，原来的向左移
            shift_reg <= {shift_reg[14:0], i2s_sdata_in};
        end
    end

    // 4. 数据锁存 (在特定 bit 数时，把移位寄存器的数据存下来)
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            left_data  <= 16'd0;
            right_data <= 16'd0;
            data_valid <= 1'b0;
        end else begin
            data_valid <= 1'b0; // 默认给 0，只在采齐时给 1 个 clk 周期的脉冲
            
            if (bclk_rising) begin
                // I2S 协议：延迟一拍开始，第 1~16 个 bit 是数据
                if (bit_cnt == 5'd16) begin
                    if (i2s_lrclk == 1'b1) begin
                        // 左声道采齐
                        left_data <= shift_reg;
                        data_valid <= 1'b1;
                    end else begin
                        // 右声道采齐
                        right_data <= shift_reg;
                        data_valid <= 1'b1;
                    end
                end
            end
        end
    end

endmodule