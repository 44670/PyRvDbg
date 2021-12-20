taskkill /f -im openocd.exe
bin\openocd.exe -c "interface cmsis-dap" -f "init.tcl"