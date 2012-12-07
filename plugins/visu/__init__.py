#!/usr/bin/env python
# vim: set encoding=utf-8 tabstop=4 softtabstop=4 shiftwidth=4 expandtab
#########################################################################
# Copyright 2012 KNX-User-Forum e.V.            http://knx-user-forum.de/
#########################################################################
#  This file is part of SmartHome.py.   http://smarthome.sourceforge.net/
#
#  SmartHome.py is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  SmartHome.py is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with SmartHome.py. If not, see <http://www.gnu.org/licenses/>.
#########################################################################

import logging
import asynchat
import asyncore
import socket
import struct
import hashlib
import base64
import threading
import json
import time
import array

import generator

logger = logging.getLogger('')


class WebSocket(asyncore.dispatcher):

    def __init__(self, smarthome, generator_dir=False, ip='0.0.0.0', port=2121):
        asyncore.dispatcher.__init__(self, map=smarthome.socket_map)
        self._sh = smarthome
        self.clients = []
        self.visu_items = {}
        self.visu_logics = {}
        self.generator_dir = generator_dir
        try:
            self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
            self.set_reuse_addr()
            self.bind((ip, int(port)))
            self.listen(5)
        except Exception, e:
            logger.error("Could not bind to socket %s:%s" % (ws_ip, ws_port))

    def _generate_pages(self, directory):
        header_file = directory + '/tpl/header.html'
        footer_file = directory + '/tpl/footer.html'
        try:
            with open(header_file, 'r') as f:
                header = f.read()
            f.closed
        except IOError, e:
            logger.error("Could not find header file: {0}".format(header_file))
            return
        try:
            with open(footer_file, 'r') as f:
                footer = f.read()
            f.closed
        except IOError, e:
            logger.error("Could not find footer file: {0}".format(footer_file))
            return
        index = header
        index += '<div data-role="page" id="index">\n'
        index += '    <div data-role="header"><h3>SmartHome</h3></div>\n'
        index += '    <div data-role="content">\n\n'
        index += '<ul data-role="listview" data-inset="true">\n'
        for item in self._sh:
            html = generator.return_tree(self._sh, item)
            item_file = "/gen/{0}.html".format(item.id())
            if 'data-sh' in html or 'data-rrd' in html:
                index += '<li><a href="{0}" data-ajax="false">{1}</a></li>\n'.format(item_file, item)
                page = header
                page += '<div data-role="page" id="{0}">\n'.format(item.id())
                page += '    <div data-role="header"><h3>{0}</h3></div>\n'.format(item)
                page += '    <div data-role="content">\n\n'
                page += html
                page += footer
                with open(directory + item_file, 'w') as f:
                    f.write(page)
                f.closed
        index += '</ul>\n' + footer
        with open(directory + '/gen/index.html', 'w') as f:
            f.write(index)
        f.closed

    def handle_accept(self):
        pair = self.accept()
        if pair is None:
            return
        else:
            sock, addr = pair
            addr = "{0}:{1}".format(addr[0], addr[1])
            logger.debug('Websocket: incoming connection from %s' % addr)
            client = WebSocketHandler(sock, self._sh.socket_map, addr, self.visu_items, self.visu_logics)
            self.clients.append(client)

    def run(self):
        self.alive = True
        if self.generator_dir:
            self._generate_pages(self.generator_dir)

    def stop(self):
        self.alive = False
        for client in self.clients:
            try:
                client.close()
            except:
                pass
        logger.debug('Closing listen')
        try:
            self.close()
        except:
            pass

    def parse_item(self, item):
        if 'visu' in item.conf:
            self.visu_items[item.id()] = item
            return self.update_item

    def parse_logic(self, logic):
        if hasattr(logic, 'visu'):
            self.visu_logics[logic.name] = logic

    def update_item(self, item, caller=None, source=None):
        data = json.dumps(['item', [item.id(), item()]])
        for client in self.clients:
            client.update(item.id(), data, source)

    def send_data(self, data):
        data = json.dumps(data)
        for client in self.clients:
            client.json_send(data)

    def dialog(self, header, content):
        data = json.dumps(['dialog', [header, content]])
        for client in self.clients:
            client.json_send(data)


class WebSocketHandler(asynchat.async_chat):

    def __init__(self, sock, socket_map, addr, items, logics):
        asynchat.async_chat.__init__(self, sock, map=socket_map)
        self.set_terminator("\r\n\r\n")
        self.parse_data = self.parse_header
        self.addr = addr
        self.ibuffer = ""
        self.header = {}
        self.monitor = []
        self.items = items
        self._lock = threading.Lock()
        self.logics = logics

    def json_send(self, data):
        logger.warning("Visu: DUMMY send to {0}: {1}".format(self.addr, data))

    def collect_incoming_data(self, data):
        self.ibuffer += data

    def initiate_send(self):
        self._lock.acquire()
        asynchat.async_chat.initiate_send(self)
        self._lock.release()

    def found_terminator(self):
        data = self.ibuffer
        self.ibuffer = ''
        self.parse_data(data)

    def update(self, path, data, source):
        if path in self.monitor:
            if self.addr != source:
                self.json_send(data)

    def difference(self, a, b):
        return list(set(b).difference(set(a)))

    def json_parse(self, data):
        logger.debug("%s sent %s" % (self.addr, repr(data)))
        try:
            command, data = json.loads(data)
        except Exception, e:
            logger.debug("Problem decoding %s from %s: %s" % (repr(data), self.addr, e))
            return

        if command == 'item':
            path = data[0]
            value = data[1]
            if path in self.items:
                self.items[path](value, 'Visu', self.addr)
            else:
                logger.info("Client %s want to update invalid item: %s" % (self.addr, path))
        elif command == 'monitor':
            if data == [None]:
                return
            for path in list(set(data).difference(set(self.monitor))):
                if path in self.items:
                    if 'visu_img' in self.items[path].conf:
                        self.json_send(json.dumps(['item', [path, self.items[path](), self.items[path].conf['visu_img']]]))
                    else:
                        self.json_send(json.dumps(['item', [path, self.items[path]()]]))
                else:
                    logger.info("Client %s requested invalid item: %s" % (self.addr, path))
            self.monitor = data
        elif command == 'logic':
            name = data[0]
            value = data[1]
            if name in self.logics:
                logger.info("Client %s triggerd logic %s with '%s'" % (self.addr, name, value))
                self.logics[name].trigger(by='Visu', value=value, source=self.addr)
        elif command == 'rrd':
            path = data[0]
            frame = str(data[1])
            if path in self.items:
                if hasattr(self.items[path], 'export'):
                    self.json_send(json.dumps(['rrd', self.items[path].export(frame)]))
                else:
                    logger.info("Client %s requested invalid rrd: %s." % (self.addr, path))
            else:
                logger.info("Client %s requested invalid item: %s" % (self.addr, path))

    def parse_header(self, data):
        for line in data.splitlines():
            key, sep, value = line.partition(': ')
            self.header[key] = value
        #logger.debug(self.header)
        if 'Sec-WebSocket-Version' in self.header:
            if self.header['Sec-WebSocket-Version'] == '13':
                self.rfc6455_handshake()
            else:
                self.handshake_failed()
        elif 'Sec-WebSocket-Key2' in self.header:
            self.parse_data = self.hixie76_handshake
            self.set_terminator(8)
        else:
            self.handshake_failed()

    def handshake_failed(self):
        logger.debug("Handshake for %s with the following header failed! %s" % (self.addr, repr(self.header)))
        self.close()

    def rfc6455_handshake(self):
        self.set_terminator(8)
        self.parse_data = self.rfc6455_parse
        self.json_send = self.rfc6455_send
        key = self.header['Sec-WebSocket-Key']
        key = base64.b64encode(hashlib.sha1(key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
        self.push('HTTP/1.1 101 Switching Protocols\r\n')
        self.push('Upgrade: websocket\r\n')
        self.push('Connection: Upgrade\r\n')
        self.push('Sec-WebSocket-Accept: %s\r\n' % key)
        #self.push('Sec-WebSocket-Protocol: chat\r\n\r\n')
        self.push('\r\n')

    def rfc6455_parse(self, data):
        offset = 0
        byte1, byte2 = struct.unpack_from('!BB', data)
        fin = (byte1 >> 7) & 0x01
        opcode = byte1 & 0x0f
        masked = (byte2 >> 7) & 0x01
        if masked:
            offset = 4
        length = byte2 & 0x7f
        if length < 126:
            offset += 2
        elif length == 126:
            offset += 4
            length = struct.unpack_from('!H', data, 2)[0]
        elif length == 127:
            offset += 8
            length = struct.unpack_from('!Q', data, 2)[0]
        read = length + offset - 8
        data = data + self.ac_in_buffer[:read]
        self.ac_in_buffer = self.ac_in_buffer[read:]
        payload = array.array('B')
        payload.fromstring(data[offset:])
        if masked:
            mask = struct.unpack_from('!BBBB', data, offset - 4)
            for i in range(len(payload)):
                payload[i] ^= mask[i % 4]
        payload = payload.tostring()
        self.json_parse(payload)
        self.set_terminator(8)

    def rfc6455_send(self, data):
        fin = 1
        rsv1 = 0
        rsv2 = 0
        rsv3 = 0
        opcode = 1  # text frame
        mask = 0
        header = chr(((fin << 7) | (rsv1 << 6) | (rsv2 << 5) | (rsv3 << 4) | opcode))
        length = len(data)
        if length < 126:
            header += chr(mask | length)
        elif length < (1 << 16):
            header += chr(mask | 126) + struct.pack('!H', length)
        elif length < (1 << 63):
            header += chr(mask | 127) + struct.pack('!Q', length)
        else:
            logger.warning("data to big: %s" % data)
            return
        self.push(header + data)

    def hixie76_send(self, data):
        self.push("\x00%s\xff" % data)

    def hixie76_parse(self, data):
        self.json_parse(data.lstrip('\x00'))

    def hixie76_handshake(self, key3):
        key1 = self.header['Sec-WebSocket-Key1']
        key2 = self.header['Sec-WebSocket-Key2']
        spaces1 = key1.count(" ")
        spaces2 = key2.count(" ")
        num1 = int("".join([c for c in key1 if c.isdigit()])) / spaces1
        num2 = int("".join([c for c in key2 if c.isdigit()])) / spaces2
        key = hashlib.md5(struct.pack('>II8s', num1, num2, key3)).digest()
        # send header
        self.push('HTTP/1.1 101 Web Socket Protocol Handshake\r\n')
        self.push('Upgrade: WebSocket\r\n')
        self.push('Connection: Upgrade\r\n')
        self.push("Sec-WebSocket-Origin: %s\r\n" % self.header['Origin'])
        self.push("Sec-WebSocket-Location: ws://%s/\r\n\r\n" % self.header['Host'])
        self.push(key)
        self.parse_data = self.hixie76_parse
        self.json_send = self.hixie76_send
        self.set_terminator("\xff")

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    ws = WebSocket('asdf', '', 3033)
    ws.run()
