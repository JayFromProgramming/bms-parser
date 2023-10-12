import traceback

import serial
from bleak.exc import BleakDBusError

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

        self._write_buffer = asyncio.Queue(maxsize=10)  # type: asyncio.Queue # Used to store the outgoing data

        self._writer_task = None  # type: asyncio.Task # Used to store the writer task
        self._reader_task = None  # type: asyncio.Task # Used to store the reader task

        self._buffer_has_data = False
        self._is_closing = False  # type: bool # Used to indicate that the connection is closing

    def _rx_callback(self, sender, data):
        # Continuously append the data to the buffer
        logging.info(f"Received {len(data)} bytes from {sender}.")
        self._buffer.extend(data)
        self._buffer_has_data = True

    async def _reader(self):
        logging.info("Reader started.")
        try:
            while not self._is_closing:
                # Read the data
                data = await self.client.read_gatt_char(self.rx_uuid)
                if len(data) == 0:
                    continue
                logging.info(f"Received {len(data)} bytes.")
                # Append the data to the buffer
                self._buffer.extend(data)
                self._buffer_has_data = True
        except asyncio.CancelledError:
            logging.warning("Reader cancelled.")
        except Exception as e:
            logging.exception(e)
            logging.error(f"Unexpected error: {e}\n{traceback.format_exc()}")
            raise e
        finally:
            logging.warning("Reader exiting.")

    async def _writer(self):
        print("Writer started.")
        logging.info("Writer started.")
        try:
            while not self._is_closing:
                await asyncio.sleep(0.3)
                # Check if there is data to send
                message = await self._write_buffer.get()
                if len(message) == 0:
                    logging.warning("Writer received empty message.")
                    continue
                # Send the data
                data = message
                print(f"Sending {len(data)} bytes.")
                logging.info(f"Sending {len(data)} bytes.")

                logging.info(f"Sent {len(data)} bytes.")
                print(f"Sent {len(data)} bytes.")
        except asyncio.CancelledError:
            logging.warning("Writer cancelled.")
        except Exception as e:
            logging.exception(e)
            logging.error(f"Unexpected error: {e}\n{traceback.format_exc()}")
            raise e
        finally:
            logging.warning("Writer exiting.")

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
        # Add the data to the write buffer
        if self._write_buffer.full():
            logging.warning("Write buffer is full.")
        else:
            logging.info(f"Adding {len(data)} bytes to the write buffer.")
            await self._write_buffer.put(data)
        # logging.info(f"Sending {len(data)} bytes.")
        # await self.client.write_gatt_char(self.tx_uuid, data, response=True)
        # logging.info(f"Sent {len(data)} bytes.")

    async def request(self, req: bytes) -> bytes:
        logging.info(f"Queueing request: {req}")
        await self.write(req)
        # Wait for the write queue to be empty
        while not self._write_buffer.empty():
            await asyncio.sleep(0.1)
        return await self.read_until()

    # Provide a non-async interface for writing
    def write_nowait(self, data: bytes):
        # Add the data to the write buffer
        if self._write_buffer.full():
            logging.warning("Write buffer is full.")
        else:
            logging.info(f"Adding {len(data)} bytes to the write buffer.")
            self._write_buffer.put_nowait(data)

    # Provide a non-async interface for requesting
    def request_sync(self, req: bytes) -> bytes:
        self.write_nowait(req)
        # Check if the write task is still running
        if self._writer_task.done():
            raise Exception("Writer task is not running.")
        return b''


class Serial:

    def __init__(self, mac_address: str):
        self.mac_address = mac_address
        self.client = None
        self.serial_conn = None
        event_loop = asyncio.get_event_loop()
        event_loop.run_until_complete(self.connect())
        if self.client is None:
            return
        self.serial_conn._writer_task = event_loop.create_task(self.serial_conn._writer())
        self.request_info()

    async def connect(self):
        devices = await BleakScanner.discover(timeout=5)
        print("Discovered devices:")
        for d in devices:
            print(f"\t{d.address} ({d.name})")
        if self.mac_address is None:
            return
        # Check if the target device is in the list of discovered devices
        target = next((d for d in devices if d.address == self.mac_address), None)
        # logging.warning(f"Unable to find device {self.mac_address}, attempting connection anyway.")
        if target is None:
            raise Exception(f"Device {self.mac_address} not found.")
        self.client = BleakClient(target)
        attempts_left = 5
        while not self.client.is_connected and attempts_left > 0:
            try:
                await self.client.connect()
            except BleakDBusError as e:
                logging.warning(f"Failed to connect because of {e}. Retrying {attempts_left} more times.")
                attempts_left -= 1
                await asyncio.sleep(1)
        if not self.client.is_connected:
            raise Exception(f"Failed to connect to device {self.mac_address}.")
        logging.info(f"Connected to {self.mac_address}.")
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
        logging.info("Started notify.")

    def _request(self, req: bytes):
        if self.client is None:
            return b''
        logging.info(f"Requesting: {req}")
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
