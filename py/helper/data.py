import serial
from py.protocol.parser import BmsPacket

from bleak import BleakClient
from bleak import BleakScanner

import asyncio

END_BYTE = b'\x77'

class BleakSerial:

    def __init__(self, bleak_client: BleakClient, service_uuid: str, char_uuid: str):
        # It is assumed that the client is already connected
        self.client = bleak_client
        self.service_uuid = service_uuid
        self.char_uuid = char_uuid
        if not self.client.is_connected:
            raise Exception("Client is not connected.")
        self._buffer = bytearray()  # type: bytearray # Used to store the incoming data
        self._buffer_lock = asyncio.Lock()  # type: asyncio.Lock # Used to lock the buffer
        self._buffer_cv = asyncio.Condition()  # type: asyncio.Condition # Used to notify the reader

        self._reader_task = None  # type: asyncio.Task # Used to store the reader task
        self._reader_task_lock = asyncio.Lock()  # type: asyncio.Lock # Used to lock the reader task

        self._write_buffer = bytearray()  # type: bytearray # Used to store the outgoing data
        self._write_buffer_lock = asyncio.Lock()  # type: asyncio.Lock # Used to lock the write buffer

        self._writer_task = None  # type: asyncio.Task # Used to store the writer task
        self._writer_task_lock = asyncio.Lock()  # type: asyncio.Lock # Used to lock the writer task

        self._is_closing = False  # type: bool # Used to indicate that the connection is closing

        self._reader_task = asyncio.create_task(self._reader())

    async def _reader(self):
        while not self._is_closing:
            data = await self.client.read_gatt_char(self.char_uuid)
            if data is None:
                continue
            async with self._buffer_lock:
                self._buffer.extend(data)
                self._buffer_cv.notify()

    async def _writer(self):
        while not self._is_closing:
            async with self._write_buffer_lock:
                if len(self._write_buffer) == 0:
                    await self._write_buffer_lock.wait()
                data = self._write_buffer
                self._write_buffer = bytearray()
            await self.client.write_gatt_char(self.char_uuid, data)

    async def read(self, n: int) -> bytes:
        async with self._buffer_lock:
            while len(self._buffer) < n:
                await self._buffer_cv.wait()
            data = self._buffer[:n]
            del self._buffer[:n]
            return data

    async def write(self, data: bytes):
        async with self._write_buffer_lock:
            self._write_buffer.extend(data)

    async def close(self):
        self._is_closing = True
        async with self._buffer_lock:
            self._buffer_cv.notify()
        async with self._write_buffer_lock:
            pass
        await self.client.disconnect()
        await self._reader_task
        await self._writer_task


class Serial:

    def __init__(self, mac_address: str):
        self.mac_address = mac_address
        self.client = None
        asyncio.run(self.connect())

    async def connect(self):
        devices = await BleakScanner.discover()
        print("Discovered devices:")
        for d in devices:
            print(d)
        if self.mac_address is None:
            return
        # Check if the target device is in the list of discovered devices
        target = next((d for d in devices if d.address == self.mac_address), None)
        if target is None:
            raise Exception(f"Device {self.mac_address} not found.")
        self.client = BleakClient(target)
        await self.client.connect()
        print("Connected to target device.")
        # Print all services
        svcs = await self.client.get_services()
        print("Services:")
        for s in svcs:
            print(s)
            # Print all characteristics
            for c in s.characteristics:
                print(f"  {c}")
        # Get the service and characteristic UUIDs
        service_uuid = next((s.uuid for s in svcs if s.uuid == '00010203-0405-0607-0809-0a0b0c0d1912'), None)
        char_uuid = next((c.uuid for c in svcs[0].characteristics if c.uuid == '00010203-0405-0607-0809-0a0b0c0d2b12'), None)
        if service_uuid is None or char_uuid is None:
            raise Exception("Service or characteristic not found.")
        self.client = BleakSerial(self.client, service_uuid, char_uuid)

    def _request(self, req: bytes):
        if self.client is None:
            return b''
        return b''

    def request_info(self) -> bytes:
        return self._request(b'\xdd\xa5\x03\x00\xff\xfdw')

    def request_cells(self) -> bytes:
        return self._request(b'\xdd\xa5\x04\x00\xff\xfcw')

    def request_hw(self) -> bytes:
        return self._request(b'\xdd\xa5\x05\x00\xff\xfbw')

    def get_cells(self):
        raw = self.request_cells()
        pkt = BmsPacket.from_bytes(raw)

        cells = [c.volt for c in pkt.body.data.cells]
        return cells

    def get_info(self):
        raw = self.request_info()
        pkt = BmsPacket.from_bytes(raw)

        i: BmsPacket.BasicInfo = pkt.body.data
        table = [
                    ('Pack Voltage', f'{i.pack_voltage.volt:.2f}', 'V'),
                    ('Cell', f'{i.cell_count}', 'count'),
                    ('Pack Current', f'{i.pack_current.amp:.2f}', 'A'),
                    ('Typ Cap', f'{i.typ_cap.amp_hour:.3f}', 'Ah'),
                    ('Remain Cap', f'{i.remain_cap.amp_hour:.3f}', 'Ah'),
                    ('Remain Percent', f'{i.remain_cap_percent}', '%'),
                    ('Cycle', f'{i.cycles:.0f}', 'count'),
                    # ('Balance', f'{i.balance_status.is_balancing}', 'bit'),
                    # ('FET', f'CHG={i.fet_status.is_charge_enabled} DIS={i.fet_status.is_discharge_enabled}', 'bit'),
                ] + [
                    ('Temp', f'{t.celsius:.2f}', '°C')
                    for t in i.temps
                ]
        balance = i.balance_status.is_balancing
        fet = {
            'charge enabled': i.fet_status.is_charge_enabled,
            'discharge enabled': i.fet_status.is_discharge_enabled,
        }
        prot_all = vars(i.prot_status)
        prot = {k: v for k, v in prot_all.items() if not k.startswith('_')}
        return (table, balance, fet, prot)
