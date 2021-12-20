transport select jtag
adapter_khz 1000
adapter_nsrst_delay 260
jtag_ntrst_delay 250

set  _ENDIAN little
set _TAP_TYPE 1234
set _CPUTAPID 0x10001fff

set _CHIPNAME fpga_spinal
jtag newtap $_CHIPNAME bridge -expected-id $_CPUTAPID -irlen 4 -ircapture 0x1 -irmask 0xF

target create $_CHIPNAME.cpu0 vexriscv -endian $_ENDIAN -chain-position $_CHIPNAME.bridge -coreid 0 -dbgbase 0xF00F0000
vexriscv readWaitCycles 12
vexriscv cpuConfigFile cpu0.yaml


poll_period 100

init
#echo "Halting processor"
soft_reset_halt