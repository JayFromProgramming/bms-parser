# BMS parser
Definitions for the generic smart battery managment system protocol. 
It is written with [Kaitai Struct](https://kaitai.io/), which can used to export parsers in different languages, some examples are in `py` for Python. `main.py` allows logging to a file or MongoDB instance, see the parameters.

`py/tui` contains a terminal based UI:
![overview TUI animated](tui.gif)

It depends on `rich` and `kaitaistruct` (runtime) module, then it can be launched (from the root dir) with:
```
python -m py.tui.main /path/to/tty
```

This fork is the same as the original repository except for TUI is now runs on `pyserial` commands for cross-platform support. 

### Non Python Parsing
For other languages you can use the compiler, for example:
```terminal
$ cd kaitai
$ kaitai-struct-compiler -t java battery_management_system_protocol.ksy
```
(Options for -t: graphviz, csharp, rust, all, perl, java, go, cpp_stl, php, lua, python, nim, html, ruby, construct, javascript)

`dumps` contains DSView logic analyzer captures of the protocol. In `decoder/bms` includes a protocol decoder for DSView or sigrok Pulseview, you can try it out with the captures.

Read more about the protocol: https://blog.ja-ke.tech/2020/02/07/ltt-power-bms-chinese-protocol.html
