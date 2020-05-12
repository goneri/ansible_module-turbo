from asyncio import coroutine, start_unix_server, Task
import argparse
import asyncio

import aiohttp


import json
import collections

import os
import signal
import sys
sys.path.append('/home/goneri/.ansible/collections/ansible_collections/vmware/vmware_rest')

import importlib

import signal

from asyncio import get_event_loop

import openstack
import q
cloud = openstack.connect()


def fork_process():
    '''
    This function performs the double fork process to detach from the
    parent process and execute.
    '''
    pid = os.fork()

    if pid == 0:
        # Set stdin/stdout/stderr to /dev/null
        fd = os.open(os.devnull, os.O_RDWR)

        # clone stdin/out/err
        for num in range(3):
            if fd != num:
                os.dup2(fd, num)

        # close otherwise
        if fd not in range(3):
            os.close(fd)

        # Make us a daemon
        pid = os.fork()

        # end if not in child
        if pid > 0:
            os._exit(0)

        # get new process session and detach
        sid = os.setsid()
        if sid == -1:
            raise Exception("Unable to detach session while daemonizing")

        # avoid possible problems with cwd being removed
        os.chdir("/")

        pid = os.fork()
        if pid > 0:
            os._exit(0)
    else:
        exit(0)
    return pid


class AnsibleVMwareTurboMode():

    def __init__(self):
        self.connector = aiohttp.TCPConnector(limit=20, ssl=False)
        self.sessions = collections.defaultdict(dict)
        self.socket_path = None
        self.ttl = None


    async def open_session(self, hostname, auth):
        if not self.sessions[hostname].get(auth):
            q("Open session!")
            async with aiohttp.ClientSession(connector=self.connector, connector_owner=False) as session:
                async with session.post("https://{hostname}/rest/com/vmware/cis/session".format(hostname=hostname), auth=auth) as resp:
                    q(resp.status)
                    json = await resp.json()
                    q(json)
                    session_id = json['value']
                    self.sessions[hostname][auth] = aiohttp.ClientSession(connector=self.connector, headers={"vmware-api-session-id": session_id}, connector_owner=False)
        return self.sessions[hostname][auth]

    async def ghost_killer(self):
        q("start watcher")
        await asyncio.sleep(self.ttl)
        q("DIE!!!")
        self.stop()


    async def handle(self, reader, writer):
        q("Handling connection")

        self._watcher.cancel()
        self._watcher = self.loop.create_task(self.ghost_killer())

        raw_data = await reader.read(1024*10)
        if not raw_data:
            return
        q(raw_data, "received")
        try:
            module_name, params = json.loads(raw_data)


            module_class = importlib.import_module('plugins.module_libs.{}'.format(module_name))
            module = module_class.Module()
            q(module)
            for k, v in params.items():
                module.params[k] = v
            if module_name.startswith("os_"):
                result = await module.main(openstack, cloud)
                q(result)
            else:


                hostname = params["hostname"]
                auth = aiohttp.BasicAuth(
                        params["username"],
                        params["password"],
                        )

                session = await self.open_session(hostname, auth)
                result = await module.main(session)
            if result == "":
                result = {}
            q(result)
            writer.write(json.dumps(result).encode())
            writer.close()
        except json.decoder.JSONDecodeError as e:
            q(e)
            pass

    def start(self):
        self.loop = get_event_loop()
        self.loop.add_signal_handler(signal.SIGTERM, self.stop)
        self._watcher = self.loop.create_task(self.ghost_killer())


        self.loop.create_task(start_unix_server(self.handle, path=self.socket_path, loop=self.loop))
        self.loop.run_forever()

    def stop(self):
        os.unlink(self.socket_path)
        self.loop.stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Start a background daemon.')
    parser.add_argument('--socket-path', default=os.environ['HOME'] + '/.ansible/turbo_mode.socket')
    parser.add_argument('--ttl', default=15, type=int)

    args = parser.parse_args()

    fork_process()
    server = AnsibleVMwareTurboMode()
    server.socket_path = args.socket_path
    server.ttl = args.ttl
    server.start()
