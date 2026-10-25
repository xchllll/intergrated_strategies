# PYNQ-Z2 PL system clock supplied by processing_system7_0 FCLK_CLK0.

# ADAU1761 I2S pins (same mapping as the PYNQ-Z2 reference design).
set_property PACKAGE_PIN R18 [get_ports i2s_bclk_0]
set_property IOSTANDARD LVCMOS33 [get_ports i2s_bclk_0]

set_property PACKAGE_PIN T17 [get_ports i2s_lrclk_0]
set_property IOSTANDARD LVCMOS33 [get_ports i2s_lrclk_0]

set_property PACKAGE_PIN F17 [get_ports i2s_sdata_0]
set_property IOSTANDARD LVCMOS33 [get_ports i2s_sdata_0]

set_property PACKAGE_PIN G18 [get_ports i2s_sdata_in_0]
set_property IOSTANDARD LVCMOS33 [get_ports i2s_sdata_in_0]

set_property PACKAGE_PIN U5 [get_ports au_mclk_0]
set_property IOSTANDARD LVCMOS33 [get_ports au_mclk_0]
