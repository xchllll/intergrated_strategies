module i2s_rx (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        i2s_bclk,
    input  wire        i2s_lrclk,
    input  wire        i2s_sdata_in,
    output reg signed [15:0] left_data,
    output reg signed [15:0] right_data,
    output     signed [15:0] mix_data,     // 左右声道平均，供解调使用
    output reg               data_valid
);

    // ------------------------------------------------------------------
    // 左右声道平均：(L+R)>>>1
    // 目的：夹具接 Tip 或 Ring 都能测到，不再依赖"解调读哪一路"。
    // 注意：两个声道在 I2S 帧里相差半个采样(1/(2*fs))，所以这相当于一个
    //       两抽头平均滤波，幅频有 |cos(pi*f/(2*fs))| 的梳状响应
    //       （20 kHz 处约 -1.9 dB）。标定与测量走同一条路，会被校准抵消。
    // ------------------------------------------------------------------
    reg signed [16:0] mix_sum;
    // 注意：Verilog 里 a+b 的结果宽度取操作数最大值(16位)，两个 16 位数相加
    // 会先溢出。所以必须先把两边符号扩展到 17 位再相加。
    wire signed [16:0] left_ext  = {left_data[15],  left_data};
    wire signed [16:0] right_ext = {right_data[15], right_data};
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            mix_sum <= 17'sd0;
        else
            mix_sum <= left_ext + right_ext;
    end
    assign mix_data = mix_sum >>> 1;

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

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bit_cnt    <= 5'd0;
            shift_reg  <= 16'd0;
            left_data  <= 16'sd0;
            right_data <= 16'sd0;
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
                    if (i2s_lrclk) begin
                        left_data  <= candidate;
                        data_valid <= 1'b1;
                    end else begin
                        right_data <= candidate;
                    end
                end
                bit_cnt <= bit_cnt + 5'd1;
            end
        end
    end

endmodule
