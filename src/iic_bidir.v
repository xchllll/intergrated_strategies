module iic_bidir (
    output wire scl_i,
    input  wire scl_o,
    input  wire scl_t,
    inout  wire scl_io,
    output wire sda_i,
    input  wire sda_o,
    input  wire sda_t,
    inout  wire sda_io
);

IOBUF scl_iobuf (
    .O(scl_i),
    .IO(scl_io),
    .I(scl_o),
    .T(scl_t)
);

IOBUF sda_iobuf (
    .O(sda_i),
    .IO(sda_io),
    .I(sda_o),
    .T(sda_t)
);

endmodule
