`timescale 1ns / 1ps

module tb_i2s_tx;

    reg        clk;
    reg        rst_n;
    reg [15:0] audio_data_L;
    reg [15:0] audio_data_R;

    wire bclk;
    wire lrclk;
    wire sdata;

    integer errors;
    integer slot_index;
    integer left_frames;

    time last_bclk_rise;
    time last_lrclk_edge;
    reg  have_bclk_rise;
    reg  have_lrclk_edge;
    reg  expected_sdata;

    i2s_tx dut (
        .clk          (clk),
        .rst_n        (rst_n),
        .audio_data_L (audio_data_L),
        .audio_data_R (audio_data_R),
        .bclk         (bclk),
        .lrclk        (lrclk),
        .sdata        (sdata)
    );

    // 100MHz 系统时钟，周期 10ns。
    initial begin
        clk = 1'b0;
        forever #5 clk = ~clk;
    end

    // 检查 bclk 周期、发送顺序和补零位置。
    always @(posedge bclk) begin
        if (rst_n) begin
            if (have_bclk_rise &&
                (($time - last_bclk_rise) != 320)) begin
                $display("ERROR: bclk period = %0t ns, expected 320 ns",
                         $time - last_bclk_rise);
                errors = errors + 1;
            end

            last_bclk_rise = $time;
            have_bclk_rise = 1'b1;

            if ((slot_index == 0) || (slot_index > 16))
                expected_sdata = 1'b0;
            else if (lrclk)
                expected_sdata = audio_data_L[16 - slot_index];
            else
                expected_sdata = audio_data_R[16 - slot_index];

            if (sdata !== expected_sdata) begin
                $display("ERROR at %0t ns: lrclk=%b slot=%0d sdata=%b expected=%b",
                         $time, lrclk, slot_index,
                         sdata, expected_sdata);
                errors = errors + 1;
            end

            if (slot_index == 31)
                slot_index = 0;
            else
                slot_index = slot_index + 1;
        end
    end

    // 检查 lrclk 每半个周期的宽度：32 × 320ns = 10240ns。
    always @(lrclk) begin
        if (rst_n) begin
            if (have_lrclk_edge &&
                (($time - last_lrclk_edge) != 10240)) begin
                $display("ERROR: lrclk half-frame = %0t ns, expected 10240 ns",
                         $time - last_lrclk_edge);
                errors = errors + 1;
            end

            last_lrclk_edge = $time;
            have_lrclk_edge = 1'b1;

            if (lrclk)
                left_frames = left_frames + 1;
        end
    end

    initial begin
        $dumpfile("tb_i2s_tx.vcd");
        $dumpvars(0, tb_i2s_tx);

        errors          = 0;
        slot_index      = 0;
        left_frames     = 0;
        have_bclk_rise  = 1'b0;
        have_lrclk_edge = 1'b0;
        last_bclk_rise  = 0;
        last_lrclk_edge = 0;

        rst_n        = 1'b0;
        audio_data_L = 16'hAAAA;
        audio_data_R = 16'h5555;

        #100;
        rst_n = 1'b1;

        // 仿真 2ms，约包含 97 个完整 lrclk 周期。
        #2_000_000;

        if (errors == 0 && left_frames >= 2)
            $display("PASS: I2S timing and data order verified; left frames = %0d",
                     left_frames);
        else
            $display("FAIL: errors = %0d, left frames = %0d",
                     errors, left_frames);

        $finish;
    end

endmodule