import socket
import os
import sys

import json
import time
import q

from ansible.module_utils.basic import AnsibleModule


class AnsibleTurboModule(AnsibleModule):

    def __init__(self, module_name, **kwargs):
        super().__init__(**kwargs)
        self.module_name = module_name
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket_path = os.environ['HOME'] + '/.ansible/turbo_mode.socket'
        self._running = None

    def start_daemon(self):
        import subprocess
        import sys
        if self._running:
            return
        q("Starting daemon")
        p = subprocess.Popen([sys.executable, '-m', 'ansible_module.turbo.server', '--fork', '--socket-path', self._socket_path, '--extra-sys-path', sys.path[0]], close_fds=True)
        self._running = True
        p.communicate()
        q("Daemon started!")
        return

    def connect(self):
        for attempt in range(100, -1, -1):
            try:
                q("Try to connect")
                self._socket.connect(self._socket_path)
                q("Connected!")
                return
            except (ConnectionRefusedError, FileNotFoundError) as e:
                self.start_daemon()
                if attempt == 0:
                    raise
            time.sleep(0.01)



    def run(self):
        self.connect()
        result = dict(changed=False, original_message='', message='')
        data = [self.module_name, self.params]
        q(data)
        self._socket.send(json.dumps(data).encode())
        raw = self._socket.recv((1024 * 10)).decode()
        q(raw)
        self._socket.close()
        result = json.loads(raw)
        q("Exit!")
        self.exit_json(**result)



