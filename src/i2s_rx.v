module i2s_rx (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        i2s_bclk,
    input  wire        i2s_lrclk,
    input  wire        i2s_sdata_in,
    output reg signed [15:0] left_data,
    output reg signed [15:0] right_data,
    output     signed [15:0] mix_data,     // 当前帧左右声道相加（饱和），供解调使用
    output reg               data_valid
);

    // ------------------------------------------------------------------
    // 当前帧的 L+R。
    // 关键：在右声道采样点用“当前左声道 + 当前右声道”更新 mix_sum，并在
    // 同一拍拉高 data_valid。这样 top 在 data_valid 后一拍读到的 mix_data
    // 与 data_valid 属于同一个完整采样帧，不会像旧实现那样读到上一帧。
    // ------------------------------------------------------------------
    reg signed [16:0] mix_sum;
    wire signed [16:0] left_ext = {left_data[15], left_data};

    // Synchronize and detect BCLK rising edges.
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
    wire bclk_rising = bclk_d1 && !bclk_d2;

    // The bit counter is aligned to LRCLK rather than free-running.
    reg lrclk_d;
    wire lrclk_changed = i2s_lrclk ^ lrclk_d;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            lrclk_d <= 1'b1;
        else
            lrclk_d <= i2s_lrclk;
    end

    reg [4:0] bit_cnt;
    reg [15:0] shift_reg;
    wire [15:0] candidate = {shift_reg[14:0], i2s_sdata_in};
    wire signed [16:0] candidate_ext = {candidate[15], candidate};

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bit_cnt    <= 5'd0;
            shift_reg  <= 16'd0;
            left_data  <= 16'sd0;
            right_data <= 16'sd0;
            mix_sum    <= 17'sd0;
            data_valid <= 1'b0;
        end else begin
            data_valid <= 1'b0;

            if (lrclk_changed) begin
                // Start a new 32-bit channel. The first bit is the I2S delay bit.
                bit_cnt   <= 5'd0;
                shift_reg <= 16'd0;

            end else if (bclk_rising) begin
                shift_reg <= candidate;

                if (bit_cnt == 5'd16) begin
                    // At this edge the delay bit has shifted out and all 16
                    // sample bits are present in candidate.
                    if (!i2s_lrclk) begin
                        // 左声道先锁存，等右声道到达后组成完整的一帧。
                        left_data <= candidate;
                    end else begin
                        // 右声道：left_data 已经是当前帧左声道，candidate 是
                        // 当前帧右声道。用当前 L + 当前 R，同时给出 valid。
                        right_data <= candidate;
                        mix_sum    <= left_ext + candidate_ext;
                        data_valid <= 1'b1;
                    end
                end

                bit_cnt <= bit_cnt + 5'd1;
            end
        end
    end

    // 直接相加（不是平均）：实测中通常只有一个声道被驱动，相加不会损失 6 dB。
    // 和可能超出 16 位范围，这里做饱和处理（削顶而不是回绕翻转）。
    wire signed [16:0] mix_sat =
        (mix_sum > 17'sd32767)  ? 17'sd32767  :
        (mix_sum < -17'sd32768) ? -17'sd32768 : mix_sum;
    assign mix_data = mix_sat[15:0];

endmodule
