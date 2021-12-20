#!/usr/bin/env python3

# This is a simple GDB Remote Serial Protocol (GDB RSP) client for debugging RISCV processors.


from PySide6 import QtWidgets, QtGui, QtCore
from PySide6.QtWidgets import QTableWidgetItem, QApplication, QDialog, QLineEdit, QMainWindow, QPushButton, QTableView, QTableWidget, QTextBrowser, QWidget
from PySide6.QtCore import QFile, Qt
from PySide6.QtUiTools import QUiLoader

import os
import sys
import socket
from io import BytesIO
from binascii import hexlify, unhexlify
import subprocess
import html
import time
import select
import traceback
import struct


# The Client of the GDB RSP Protocol
class RSPClient():

    def __init__(self, ui) -> None:
        self._socket: socket.socket = None
        self._connected: bool = False
        self._ui = ui
        self._pendingData:bytes = b''

    def connect(self, host, port):
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.connect((host, port))
        # Set NODELAY
        self._socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # Set recv timeout to 10 seconds
        self._socket.settimeout(10)
        if self._rspGetAck() != True:
            print('No ack from GDB server')
            self._socket.close()
            return False
        self._connected = True
        # Enable NoAckMode
        self.rspCall(b'QStartNoAckMode', waitForAck=True)
        # Set to extended mode
        self.rspCall(b'!')
        
        # Query target xml
        xml = self.rspCall(b'qXfer:features:read:target.xml:0,9999')[1:]
        with open('target.xml', 'wb') as f:
            f.write(xml)
        self._ui.onTargetXmlUpdated(xml)
        self._ui.onStateUpdated('paused')
        return True

    def disconnect(self):
        self._connected = False
        self._socket.close()
        self._ui.onStateUpdated(None)
        return True

    """
    GDB RSP protocol basic concepts:
    1. Packet starts with "$", ends with "#" and checksum(two hex digits)
    2. "$", "#", "}" in the middle of packet is escaped by "}" following char xor
    0x20.
    3. "*" indicates the beginning of RLE encoded data (we don't support this for now).
    """

    def _rspEscape(self, data: bytes):
        # TODO: do escaping
        return data

    def _rspUnescape(self, data: bytes):
        # TODO: do unescaping
        return data

    def _rspGetAck(self):
        # Receive one byte from socket
        data = self._socket.recv(1)
        if data == b'+':
            return True
        else:
            print('Unknown Ack:', data)
            return False

    def _rspConsumePackets(self):
        ret = None
        # Receive packet
        while True:
            data = self._socket.recv(30000)
            if not data:
                print('Disconnected')
                self.disconnect()
                return None
            self._pendingData += data
            if self._pendingData[-3] == ord('#'):
                break
        while(True):
            pos = self._pendingData.find(b'#')
            if pos == -1:
                break
            pkt = self._pendingData[:pos]
            self._pendingData = self._pendingData[pos+1:]
            pos2 = pkt.find(b'$')
            if pos2 == -1:
                print('Malformed data:', pkt)
                continue
            pkt = pkt[pos2+1:]
            if len(pkt) >= 3:
                if pkt[0] == ord('T'):
                    print('Trap:', pkt)
                    newState = b'T' + pkt[1:]
                    self._ui.onStateUpdated('paused')
                    continue
                if pkt[0] == ord('O'):
                    # Print log messages
                    print('Log:', unhexlify(pkt[1:]))
                    continue
            if ret != None:
                print('Ignored:', ret)
            ret = pkt
        return ret


    def _rspCheckUnexpectedData(self):
        # Use select to check if there is data to read
        r, w, e = select.select([self._socket], [], [], 0)
        if r:
            data = self._rspConsumePackets()
            if data != None:
                print('Unexpected:', data)
            
    def _rspRecvPacket(self):
        # Receive packet
        while True:
            ret = self._rspConsumePackets()
            if ret != None:
                return ret


    def rspCall(self, data: bytes, waitForAck=False, waitForResp=True):
        # Ensure data is a bytes
        if not isinstance(data, bytes):
            data = data.encode('utf8')
        if not self._connected:
            raise Exception('Not connected')
            return False
        data = self._rspEscape(data)
        # Send the packet
        try:
            self._rspCheckUnexpectedData()
            self._socket.sendall(b'$')
            self._socket.sendall(data)
            self._socket.sendall(b'#')
            # Calculate checksum
            checksum = 0
            for b in data:
                checksum += b
            checksum &= 0xFF
            self._socket.sendall(b'%02x' % checksum)
            # Wait for ACK
            if waitForAck:
                self._rspGetAck()
            if not waitForResp:
                return True
            return self._rspUnescape(self._rspRecvPacket())
        except Exception as e:
            print('Socket error:', e)
            # print stack trace
            traceback.print_exc()
            self.disconnect()
 

    def read(self, addr, size):
        ret = self.rspCall(b'm%x,%x' % (addr, size))
        return unhexlify(ret)

    def _readUInt(self, addr, size):
        ret = self.read(addr, size)
        return int.from_bytes(ret, byteorder='little')

    def read8(self, addr):
        return self._readUInt(addr, 1)

    def read16(self, addr):
        return self._readUInt(addr, 2)

    def read32(self, addr):
        return self._readUInt(addr, 4)

    def read64(self, addr):
        return self._readUInt(addr, 8)

    def readToFile(self, addr, size, file="read.bin"):
        with open(file, 'wb') as f:
            while size > 0:
                data = self.read(addr, min(size, 4096))
                f.write(data)
                addr += len(data)
                size -= len(data)
        return True

    def write(self, addr, data):
        data = hexlify(data)
        return self.rspCall(b'M%x,%x:%s' % (addr, len(data) // 2, data))

    def _writeUInt(self, addr, uint: int, size):
        return self.write(addr, uint.to_bytes(size, byteorder='little'))

    def write8(self, addr, uint: int):
        return self._writeUInt(addr, uint, 1)

    def write16(self, addr, uint: int):
        return self._writeUInt(addr, uint, 2)

    def write32(self, addr, uint: int):
        return self._writeUInt(addr, uint, 4)

    def write64(self, addr, uint: int):
        return self._writeUInt(addr, uint, 8)

    def writeFromFile(self, addr, file="write.bin", maxSize=-1):
        with open(file, 'rb') as f:
            if maxSize == -1:
                data = f.read()
            else:
                data = f.read(maxSize)
        pos = 0
        while pos < len(data):
            if self.write(addr, data[pos:pos+4096]) == False:
                print('Write failed at: %08x' % addr)
                return False
            addr += 4096
            pos += 4096
        return True

    def go(self):
        # Continues execution of the target program
        self.rspCall(b'vCont;c', waitForResp=False)
        self._ui.onStateUpdated('running')
        return True
    
    def step(self):
        self.rspCall(b's', waitForResp=False)
        return True

    def pause(self):
        self._socket.sendall(b'\x03')
        return True

    def monitorCmd(self, cmd):
        cmd = b'qRcmd,%s' % hexlify(cmd)
        print(cmd)
        return self.rspCall(cmd)
    
    def reset(self, halt = True):
        self.monitorCmd(b'reset halt')
        time.sleep(1)
        self._ui.onStateUpdated('paused')
    
    # Poll the server for current status
    # Must be called periodically
    def poll(self):
        if not self._connected:
            return False
        try:
            self._rspCheckUnexpectedData()
        except Exception as e:
            print('Poll failed:', e)
            self.disconnect()
        return True

    def getRegs(self):
        return self.rspCall('g')

    def getOneReg(self, regID):
        return self.rspCall('p%02x' % regID)
    
    def _getTypeID(self, type):
        return {'soft':0, 'hard':1, 'read':2, 'write':3, 'access':4}[type]

    def bpadd(self, addr, type='soft', size = 4):
        type = self._getTypeID(type)
        return self.rspCall(b'Z%x,%x,%x' % (type, addr, size))

    def bpdel(self, addr, type='soft', size = 4):
        type = self._getTypeID(type)
        return self.rspCall(b'z%x,%x,%x' % (type, addr, size))
    

uiConfig = {
    'host': '127.0.0.1',
    'port': 3333
}


class UIInterface():
    def __init__(self):
        self.isPaused:bool = False
        self.PC:int = 0
        self.regs = [0] * 32
        self.disasmMemCache = {}
    
    def onTargetXmlUpdated(self, xml):
        # TODO: parse xml, rather than use hard-coded values
        RISCV_REG_NAMES = [
            'PC',
            'ra',
            'sp', 'gp',
            'tp', 't0', 't1', 't2',
            's0', 's1', 'a0', 'a1', 'a2', 'a3', 'a4', 'a5', 'a6', 'a7',
            's2', 's3', 's4', 's5', 's6', 's7', 's8', 's9', 's10', 's11',
            't3', 't4', 't5', 't6',
        ]
        # Initialize the tableRegs Widget
        tableRegs.setRowCount(len(RISCV_REG_NAMES))
        tableRegs.setColumnCount(2)
        tableRegs.setHorizontalHeaderLabels(['Name', 'Value'])
        # Insert items
        for i, name in enumerate(RISCV_REG_NAMES):
            tableRegs.setItem(i, 0, QtWidgets.QTableWidgetItem(name))
            tableRegs.setItem(i, 1, QtWidgets.QTableWidgetItem('?'))
            tableRegs.setRowHeight(i, 10)
        # Hide the vertical header
        tableRegs.verticalHeader().setVisible(False)

    def onStateUpdated(self, newState):
        print("State updated:", newState)
        isPaused = newState == 'paused'
        isConnected = (newState != None)
        self.isPaused = isPaused
        btnPause.setText('Go' if isPaused else 'Pause')
        text = 'Paused' if isPaused else 'Running'
        if newState == None:
            text = 'Disconnected'
        window.setWindowTitle('PyRvDbg (%s)' % text)
        if isPaused and client._connected:
            self.onPaused()

    def parseRegs(self, hex, bitWidth = 32):
        # TODO: handle 64-bit processors
        buf = unhexlify(hex)
        regs = []
        for i in range(0, len(buf), bitWidth // 8):
            regs.append(int.from_bytes(buf[i:i+bitWidth//8], byteorder='little'))
        return regs
    
    def onPaused(self):
        # update PC, assume the PC index is 32
        self.PC = self.parseRegs(client.getOneReg(32))[0]
        self.regs = self.parseRegs(client.getRegs())
        print(self.regs)
        for i in range(1, 32):
            # Update regs to the UI
            tableRegs.setItem(i, 1, QTableWidgetItem(hex(self.regs[i])))
        # Update PC to table row 0
        tableRegs.setItem(0, 1, QTableWidgetItem(hex(self.PC)))
        txtDisasmAddr.setText(hex(self.PC))

    def onPauseGo(self):
        if ui.isPaused:
            client.go()
        else:
            client.pause()

    def setup(self):
        window.btnPause.clicked.connect(self.onPauseGo)
        window.actionReset.triggered.connect(client.reset)
        window.btnDisasm.clicked.connect(self.updateDisasm)
        window.btnStep.clicked.connect(client.step)

    def updateDisasm(self):
        addr = int(txtDisasmAddr.text(), 16)
        data = client.read(addr, 1024)
        with open('disasm.tmp', 'wb') as f:
            f.write(data)
        args = ['--adjust-vma', '0x%x' % addr, '-m', 'riscv', '-b', 'binary', '-D', 'disasm.tmp']
        # Start bin\riscv64-unknown-elf-objdump.exe and read stdout 
        p = subprocess.Popen(['bin\\riscv64-unknown-elf-objdump.exe'] + args, stdout=subprocess.PIPE)
        # Wait for the process to finish
        p.wait()
        disasm = p.stdout.read().decode('utf-8')
        disasm = '\n'.join(disasm.split('\n')[7:])
        # Update the disasm text
        tbDisasmResult.setText(disasm)




def escapeForQTextBrowser(text):
    return html.escape(text, quote=False)

def onConsoleCmd():
    # Get text from qlineedit
    cmd = txtConsoleCmd.text()
    if cmd == "":
        return
    # Clear the text
    txtConsoleCmd.clear()
    # Append to the qtextbrowser
    tbConsole.append("<p>%s</p>" % escapeForQTextBrowser(":" + cmd))
    try:
        ret = str(eval(cmd))
        tbConsole.append("<b>%s</b>" % escapeForQTextBrowser(ret))
    except Exception as e:
        tbConsole.append("<b>%s</b>" % escapeForQTextBrowser(str(e)))

def log(*args):
    # Get the text to print
    text = " ".join(map(str, args))
    # Append to the qtextbrowser
    tbConsole.append("<p>%s</p>" % escapeForQTextBrowser(text))
    print(text)

def msgBox(msg):
    msgBox = QtWidgets.QMessageBox()
    msgBox.setText(msg)
    msgBox.exec_()

def doConnect():
    global client
    ret = False
    try:
        ret = client.connect(uiConfig['host'], uiConfig['port'])
    except(Exception) as e:
        log(e)
        traceback.print_exc()
        # Message box
        msgBox('Failed to connect to %s:%d' % (uiConfig['host'], uiConfig['port']))

def onDialogAccept():
    # Check which radio button is checked
    if connectDialog.radioConnect.isChecked():
        doConnect()
    else:
        # Check if init.tcl exists
        if not os.path.exists('init.tcl'):
            msgBox('init.tcl not found.\nPlease create init.tcl to configure for your target first.\nYou may find useful templates in examples folder.')
            return
        # Create a new openocd instance
        args = ["-c", "interface %s" % connectDialog.txtInterface.text(), "-f", "init.tcl"]
        # Spawn the openocd process
        p = subprocess.Popen(["bin/openocd"] + args)
        time.sleep(1)
        # Check if the process is running
        if p.poll() is not None:
            msgBox('Failed to start openocd')
            return
        else:
            doConnect()

def isSocketConnectable(host, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Set timeout to 1 second
        s.settimeout(1)
        s.connect((host, port))
        s.close()
        return True
    except(Exception) as e:
        return False

if __name__ == "__main__":
    ui = UIInterface()
    client: RSPClient = RSPClient(ui)
    app = QApplication([])
    loader = QUiLoader()
    ui_file = QFile("ui/form.ui")
    ui_file.open(QFile.ReadOnly)
    window = loader.load(ui_file)
    ui_file.close()
    tableRegs: QTableWidget = window.tableRegs
    btnPause: QPushButton = window.btnPause
    txtConsoleCmd : QLineEdit= window.txtConsoleCmd
    tbConsole: QTextBrowser = window.tbConsole
    txtDisasmAddr: QLineEdit = window.txtDisasmAddr
    tbDisasmResult: QTextBrowser = window.tbDisasmResult
    ui.setup()
    ui.onStateUpdated(None)
    # Extract all the client function to global namespace
    cmdList = []
    for name in dir(client):
        if not name.startswith('_'):
            globals()[name] = getattr(client, name)
            cmdList.append(name)
    tbConsole.append('Command list:')
    tbConsole.append(', '.join(cmdList))
    txtConsoleCmd.returnPressed.connect(onConsoleCmd)
    window.show()
    window.actionAbout_Qt.triggered.connect(app.aboutQt)
    # Load connection settings dialog (connect.ui)
    ui_file = QFile("ui/connect.ui")
    ui_file.open(QFile.ReadOnly)
    connectDialog:QDialog = loader.load(ui_file)
    ui_file.close()
    connectDialog.show()
    if isSocketConnectable(uiConfig['host'], uiConfig['port']):
        connectDialog.radioConnect.setChecked(True)
    else:
        connectDialog.radioNew.setChecked(True)
    # Handle connect dialog accept
    connectDialog.accepted.connect(onDialogAccept)
    # Create a timer to poll the server
    timer = QtCore.QTimer()
    timer.setInterval(500)
    timer.timeout.connect(lambda: client.poll())
    timer.start()
    sys.exit(app.exec_())
