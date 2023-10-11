import serial
from py.protocol.parser import BmsPacket

from bleak import BleakClient
from bleak import BleakScanner

from loguru import logger as logging

import asyncio
import threading

END_BYTE = b'\x77'


class BleakSerial:

    def __init__(self, bleak_client: BleakClient, rx_uuid: str, tx_uuid: str):
        # It is assumed that the client is already connected
        self.client = bleak_client
        self.rx_uuid = rx_uuid
        self.tx_uuid = tx_uuid
        if not self.client.is_connected:
            raise Exception("Client is not connected.")

        self._buffer = bytearray()  # type: bytearray # Used to store the incoming data

        self._write_buffer = bytearray()  # type: bytearray # Used to store the outgoing data
        self._write_buffer_lock = asyncio.Lock()  # type: asyncio.Lock # Used to lock the write buffer

        self._writer_task = None  # type: asyncio.Task # Used to store the writer task
        self._writer_task_lock = asyncio.Lock()  # type: asyncio.Lock # Used to lock the writer task

        self._buffer_has_data = False
        self._is_closing = False  # type: bool # Used to indicate that the connection is closing

        # self._reader_task = asyncio.create_task(self._reader())
        self._writer_task = asyncio.create_task(self._writer())

    def _rx_callback(self, sender, data):
        # Continuously append the data to the buffer
        self._buffer.extend(data)
        self._buffer_has_data = True

    # async def _reader(self):
    #     while not self._is_closing:
    #         # Read the data
    #         data = await self.client.read_gatt_char(self.rx_uuid)
    #         # Append the data to the buffer
    #         self._buffer.extend(data)
    #         self._buffer_has_data = True

    async def _writer(self):
        while not self._is_closing:
            await asyncio.sleep(0.3)
            async with self._write_buffer_lock:
                if len(self._write_buffer) == 0:
                    continue
                data = self._write_buffer
                self._write_buffer = bytearray()
            await self.client.write_gatt_char(self.tx_uuid, data)
            logging.info(f"Sent {len(data)} bytes.")

    async def read_until(self, timeout=5) -> bytes:
        start_time = asyncio.get_running_loop().time()
        while start_time + timeout > asyncio.get_running_loop().time():
            # Check if the buffer has data
            if self._buffer_has_data:
                self._buffer_has_data = False
            # Return the data if we have it
            if len(self._buffer) > 0:
                data = self._buffer
                self._buffer = bytearray()
                return data
        raise TimeoutError(f"Timeout while waiting for data. Received {len(self._buffer)} bytes.")

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

    # Provide a non-async interface for writing
    def write_sync(self, data: bytes):
        asyncio.run(self.write(data))

    def read_until_sync(self) -> bytes:
        return asyncio.run(self.read_until())

    # Provide a non-async interface for requesting
    def request_sync(self, req: bytes) -> bytes:
        self.write_sync(req)
        return self.read_until_sync()


class Serial:

    def __init__(self, mac_address: str):
        self.mac_address = mac_address
        self.client = None
        self.serial_conn = None
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
        service_uuid = "0000ff0-0000-1000-8000-00805f9b34fb"
        rx = "0000ff01-0000-1000-8000-00805f9b34fb"
        tx = "0000ff02-0000-1000-8000-00805f9b34fb"
        # paired = await self.client.pair(legacy=True)
        # print(f"Paired: {paired}")
        self.serial_conn = BleakSerial(self.client, rx.lower(), tx.lower())
        await self.client.start_notify(rx.lower(), self.serial_conn._rx_callback)

    def _request(self, req: bytes):
        if self.client is None:
            return b''
        return self.serial_conn.request_sync(req)

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
