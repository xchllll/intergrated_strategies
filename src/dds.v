module dds #(
    parameter integer PHASE_W = 32,
    parameter integer ADDR_W  = 10,
    parameter integer DATA_W  = 16
)(
    input  wire                     clk,
    input  wire                     rst_n,
    input  wire                     en,
    input  wire [PHASE_W-1:0]       phase_inc,
    output reg  signed [DATA_W-1:0] sine,   // 原有：正弦
    output reg  signed [DATA_W-1:0] cosine  // 新增：余弦
);

    localparam integer TABLE_LEN = (1 << ADDR_W);
    reg [PHASE_W-1:0] phase_acc;
    reg [DATA_W-1:0] rom [0:TABLE_LEN-1];

    initial begin
        $readmemh("sine.mem", rom);
    end

    wire [ADDR_W-1:0] addr_sin;
    wire [ADDR_W-1:0] addr_cos;

    // 关键逻辑 1：正弦地址，截取高位
    assign addr_sin = phase_acc[PHASE_W-1 -: ADDR_W];
    
    // 关键逻辑 2：余弦地址，比正弦多走 256 个点（相当于超前 90 度）
    // 注意：如果地址超过了 1023，它会自动溢出回绕，这正是我们想要的（转了一圈又回来了）
    assign addr_cos = addr_sin + 10'd256; 

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            phase_acc <= {PHASE_W{1'b0}};
            sine      <= {DATA_W{1'b0}};
            cosine    <= {DATA_W{1'b0}};
        end else if (en) begin
            phase_acc <= phase_acc + phase_inc;
            sine      <= rom[addr_sin];  // 查正弦
            cosine    <= rom[addr_cos];  // 查余弦
        end
    end
endmodule