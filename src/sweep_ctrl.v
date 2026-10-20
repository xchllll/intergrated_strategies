module sweep_ctrl (
    input  wire        clk,            // Clock (系统时钟)
    input  wire        rst_n,          // Reset Negative (低电平复位)
    input  wire        start,          // Start (启动扫频)
    
    // 给 DDS 的控制信号
    output reg  [31:0] phase_inc,      // Phase Increment (频率控制字)
    
    // 给 Demod 的控制信号
    output reg         clear,          // Clear (清零积分器)
    output reg         en,             // Enable (使能积分)
    
    // 给后级的标志信号
    output reg         done,           // Done (当前频点测量完成)
    output reg  [7:0]  freq_idx        // Frequency Index (当前频点索引, 0-239)
);

    // 状态定义 (Finite State Machine: 有限状态机)
    localparam IDLE    = 2'd0;         // 待机状态
    localparam CLEAR   = 2'd1;         // 清零状态
    localparam MEASURE = 2'd2;         // 测量状态
    localparam DONE    = 2'd3;         // 完成状态

    reg [1:0]  state, next_state;      // 当前状态与下一状态
    reg [31:0] timer;                  // Timer (计时器，控制停留时间)

    // 设定积分时间：100MHz 下，等 10000 个周期 = 100us
    localparam TIME_MAX = 32'd5;   

    // ==========================================
    // 第一段：状态切换寄存器 (同步时序)
    // ==========================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            state <= IDLE;
        else
            state <= next_state;
    end

    // ==========================================
    // 第二段：次态组合逻辑 (决定下一步去哪)
    // ==========================================
    always @(*) begin
        next_state = state; // 默认保持当前状态
        case (state)
            IDLE: begin
                if (start) next_state = CLEAR;
            end
            CLEAR: begin
                next_state = MEASURE; // 清零一拍后立刻开始测量
            end
            MEASURE: begin
                if (timer >= TIME_MAX) next_state = DONE; // 时间到，去完成状态
            end
            DONE: begin
                if (freq_idx == 8'd239) next_state = IDLE; // 扫完 240 个点，回待机
                else                    next_state = CLEAR; // 否则继续测下一个点
            end
            default: next_state = IDLE;
        endcase
    end

    // ==========================================
    // 第三段：输出逻辑与计时器 (同步时序)
    // ==========================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            clear     <= 1'b0;
            en        <= 1'b0;
            done      <= 1'b0;
            timer     <= 32'd0;
            freq_idx  <= 8'd0;
            phase_inc <= 32'd42950; // 给个初始频率(1kHz)
        end else begin
            // 默认输出清零 (防止锁存器)
            clear <= 1'b0;
            en    <= 1'b0;
            done  <= 1'b0;

            case (state)
                IDLE: begin
                    timer    <= 32'd0;
                    freq_idx <= 8'd0;
                end
                
                CLEAR: begin
                    clear <= 1'b1; // 给解调器发清零脉冲
                    timer <= 32'd0; // 计时器归零
                end
                
                MEASURE: begin
                    en    <= 1'b1; // 打开使能，开始积分
                    timer <= timer + 1'b1; // 计时器开始数数
                    // 这里先简单让频率慢慢增加，后面再改成查表
                    phase_inc <= phase_inc + 32'd10000; 
                end
                
                DONE: begin
                    done <= 1'b1; 
                    // 增加判断：只有小于 239 的时候才加 1，超过或等于 239 就保持不动
                    if (freq_idx < 8'd239) begin
                        freq_idx <= freq_idx + 1'b1;
                    end
                end
            endcase
        end
    end

endmodule