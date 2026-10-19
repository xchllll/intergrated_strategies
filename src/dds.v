module dds #(
    parameter integer PHASE_W = 32,
    parameter integer ADDR_W  = 10,
    parameter integer DATA_W  = 16
)(
    input  wire                     clk,
    input  wire                     rst_n,
    input  wire                     en,
    input  wire [PHASE_W-1:0]       phase_inc,
    output reg  signed [DATA_W-1:0] sine
);
    localparam integer TABLE_LEN = (1 << ADDR_W);
    reg [PHASE_W-1:0] phase_acc;
    reg [DATA_W-1:0] rom [0:TABLE_LEN-1];
    initial begin
        $readmemh("sine.mem", rom);
    end
    wire [ADDR_W-1:0] addr = phase_acc[PHASE_W-1 -: ADDR_W];
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            phase_acc <= {PHASE_W{1'b0}};
            sine      <= {DATA_W{1'b0}};
        end else if (en) begin
            phase_acc <= phase_acc + phase_inc;
            sine      <= rom[addr];
        end
    end
endmodule