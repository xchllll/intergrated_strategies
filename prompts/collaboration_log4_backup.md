1.P0 前置自检
————————
*test_ps_ctrl.py 运行输出*
....
----------------------------------------------------------------------
Ran 4 tests in 0.006s

OK
————————
*calibration.py 运行输出*
检：acc_I=1737733 acc_Q=1737733 (N=100) -> mag=1.5000 V, phase=45.00 deg
期望：mag=1.5000, phase=45.00（与 demod_sim.py 一致）
四态校准(1kHz) -> direct=(1.0, 0.0), buffer=(1.0, 0.0)
期望：两路径 mag_gain=1.0, phase_offset=0.0
直连校准后 -> mag=1.5000, phase=45.00
缓冲校准后 -> mag=1.5000,pyt phase=45.00

--- 校准闭环自检（mock=True，不依赖硬件）---
[cal] f=   500.00 Hz | direct gain=1.0000 off=0.00° | buffer gain=1.0000 off=0.00°
[cal] f=  1000.00 Hz | direct gain=1.0000 off=0.00° | buffer gain=1.0000 off=0.00°
[cal] f=  2000.00 Hz | direct gain=1.0000 off=0.00° | buffer gain=1.0000 off=0.00°
四态校准完成：3 个频点已写入 CAL_TABLE，校准表已保存至 C:\Users\13692\AppData\Local\Temp\tmp6e2na3bv\cal_table_test.json
校准状态 -> calibrated=True, 频点数=3, 范围=500~2000 Hz, 路径={'direct': 3, 'buffer': 3}
状态说明 -> 校准表已就绪：3 个频点（500~2000 Hz）
已加载校准表：C:\Users\13692\AppData\Local\Temp\tmp6e2na3bv\cal_table_test.json（3 个频点）
重新加载表(loaded=True)后 apply_calibration -> mag=1.5000, phase=45.00
（期望：mag=1.5000, phase=45.00）
————————