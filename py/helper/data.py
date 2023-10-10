import serial
from py.protocol.parser import BmsPacket

from bleak import BleakClient
from bleak import BleakScanner
import asyncio

END_BYTE = b'\x77'

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
        print(f"Connecting to {self.mac_address}...")
        self.client = BleakClient(self.mac_address, disconnected_callback=self.on_disconnect)
        await self.client.connect()
        print("Connected!")
        # Print services
        for s in self.client.services:
            print(s)
            # Print characteristics
            for c in s.characteristics:
                print(f'  {c}')

    def on_disconnect(self, client):
        print("Disconnected!")
        self.client = None

    def _request(self, req: bytes):
        if self.client is None:
            return b''
        asyncio.run(self.client.write_gatt_char('00010203-0405-0607-0809-0a0b0c0d1912', req))
        raw = asyncio.run(self.client.read_gatt_char('00010203-0405-0607-0809-0a0b0c0d1912'))
        print(raw)
        return raw

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
